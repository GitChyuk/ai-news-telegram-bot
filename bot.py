#!/usr/bin/env python3
"""Агрегатор AI-новостей: опрашивает RSS-ленты и постит новое в Telegram-канал."""

import html
import json
import logging
import os
import re
import signal
import sys
import threading
import time
import urllib.parse
from calendar import timegm
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import feedparser
import requests

LOG = logging.getLogger("ai-news")

UA = "Mozilla/5.0 (compatible; ai-news-bot/1.0)"
API = "https://api.telegram.org/bot{token}/sendMessage"
HTTP_TIMEOUT = 15

DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_TIMEOUT = 25
TRANSLATE_PROMPT = (
    "Ты переводишь заголовки новостей об ИИ на русский язык. "
    "Названия компаний, моделей и продуктов оставляй латиницей (OpenAI, GPT-5, Llama). "
    "Не добавляй ничего от себя, не ставь точку в конце. "
    "В ответе — только перевод одной строкой."
)

DESCRIBE_PROMPT = (
    "Ты — редактор Telegram-канала об искусственном интеллекте. "
    "По заголовку и краткому описанию новости из RSS-ленты сделай пост на русском, "
    "чтобы читатель понял суть НЕ переходя на сайт. "
    "Дай: (1) понятный русский заголовок; (2) связное описание в 2–4 предложениях — "
    "что произошло, кто участник, почему это важно. "
    "Пиши простым живым языком, без канцелярита и кликбейта. "
    "Названия компаний, моделей и продуктов оставляй латиницей (OpenAI, GPT-5, Qwen). "
    "Опирайся ТОЛЬКО на данные из заголовка и описания, ничего не выдумывай. "
    "Если описания нет — ограничься 1–2 предложениями строго по заголовку. "
    "Пиши так, чтобы пост хотелось переслать другу: конкретика и цифры из текста, "
    "живые формулировки, никакой воды. "
    "Дай также: (3) короткий вывод в одно предложение — ПОЧЕМУ это важно или "
    "что меняет для читателя (поле takeaway); (4) одну рубрику из списка "
    "[модели, бизнес, исследования, инструменты, безопасность, железо, "
    "регулирование, общество] (поле tag). "
    "(5) Поле hook — один короткий вопрос читателю по теме новости, чтобы "
    "захотелось ответить; если вопрос не задать естественно — пустая строка. "
    "(6) Поле howto — 1–2 практичных предложения «как применить себе», "
    "ТОЛЬКО если новость про конкретный инструмент, сервис или приём; "
    "иначе — пустая строка. "
    "Ответь СТРОГО в формате JSON: "
    "{\"title\": \"...\", \"summary\": \"...\", \"takeaway\": \"...\", \"tag\": \"...\", "
    "\"hook\": \"...\", \"howto\": \"...\"} "
    "без пояснений."
)
CATEGORY_TAGS = {"модели", "бизнес", "исследования", "инструменты",
                 "безопасность", "железо", "регулирование", "общество"}

# --- Утренний пост «Главное за день» ---
DAILY_WINDOW_HOURS = 24
DAILY_MAX = 5
DAILY_PER_SOURCE_CAP = 25

# --- Воскресный «Топ-5 недели» ---
WEEKLY_WINDOW_HOURS = 24 * 7
WEEKLY_MAX = 5
WEEKLY_PER_SOURCE_CAP = 60

# --- Подборка (3 прогона в день, см. ai-news-digest.timer) ---
DIGEST_WINDOW_HOURS = 5       # окно: интервал между прогонами + запас на пропуск
DIGEST_MAX_POSTS = 2          # пиковых поста за прогон: всего 2–4 «пиковых» в день
DIGEST_PER_SOURCE_CAP = 15    # не дать arXiv затопить список кандидатов
DIGEST_MAX_CANDIDATES = 80    # верхний предел на промпт курирования
CURATE_TIMEOUT = 90
CURATE_PROMPT = (
    "Ты — редактор Telegram-канала об искусственном интеллекте для широкой аудитории. "
    "Тебе дают пронумерованный список заголовков новостей за последние сутки. "
    "Выбери ТОЛЬКО те, что действительно интересны и важны обычным людям, "
    "следящим за ИИ: крупные релизы моделей и продуктов, заметные прорывы, "
    "значимые события индустрии (сделки, запуски, регулирование, деньги), "
    "громкие инциденты и споры. "
    "Отбрасывай узкоспециальные научные препринты, мелкие технические обновления, "
    "рекламу, вакансии, туториалы и всё малозначимое. "
    "ВАЖНО про дубли: несколько заголовков часто описывают ОДНО И ТО ЖЕ событие "
    "разными словами или на разных языках (например одинаковый релиз модели в "
    "англо- и русскоязычной ленте). Сгруппируй такие по смыслу и оставь только "
    "ОДИН лучший заголовок на каждое реальное событие. "
    "Лучше выбрать мало, чем много: если стоящего нет вовсе — верни пустой список. "
    "Ответь СТРОГО в формате JSON: {\"picks\": [номера]} — номера по убыванию "
    f"важности, максимум {DIGEST_MAX_POSTS} штук, без каких-либо пояснений."
)
TRACKING_PREFIXES = ("utm_", "yclid", "gclid", "fbclid", "_openstat", "ref_src")

# Стейта должно хватать с запасом на суммарный объём всех лент, иначе FIFO
# вытеснит запись, которая всё ещё висит в ленте, и она уйдёт в канал повторно.
STATE_CAP = 20000
# Записи старше этого возраста не постим (страховка от бэкфилла при потере стейта).
MAX_AGE_DAYS = 3
# Потолок постов за цикл: arXiv умеет выложить сотню статей разом.
MAX_POSTS_PER_CYCLE = 30

MIN_POST_INTERVAL = 3.0
MAX_POSTS_PER_MINUTE = 18


def env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def load_config():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    channel = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHANNEL_ID", channel)) if not v]
    if missing:
        sys.exit("Не заданы переменные окружения: " + ", ".join(missing))

    base = os.path.dirname(os.path.abspath(__file__))
    return {
        "token": token,
        "channel": channel,
        "interval": env_int("POLL_INTERVAL", 60),
        "state_file": os.environ.get("STATE_FILE", os.path.join(base, "seen.json")),
        "feeds_file": os.environ.get("FEEDS_FILE", os.path.join(base, "feeds.json")),
        # Без ключа бот просто постит оригинальные заголовки, это не ошибка.
        "ds_key": os.environ.get("DEEPSEEK_API_KEY", "").strip(),
        "ds_model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip(),
        # Ссылка для призыва подписаться (работает, когда пост репостят).
        "channel_link": ("https://t.me/" + channel.lstrip("@")) if channel.startswith("@") else "",
    }


def normalize(url):
    """Каноническая форма ссылки — ключ дедупликации."""
    try:
        p = urllib.parse.urlsplit(url.strip())
        query = urllib.parse.urlencode(
            [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith(TRACKING_PREFIXES)]
        )
        return urllib.parse.urlunsplit(
            (p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", query, "")
        )
    except ValueError:
        return url.strip()


class Seen:
    """Множество отправленных ключей с FIFO-вытеснением и персистентностью."""

    def __init__(self, path, cap=STATE_CAP):
        self.path, self.cap = path, cap
        self.order, self.keys = deque(), set()
        try:
            with open(self.path, encoding="utf-8") as f:
                for key in json.load(f):
                    self._push(key)
        except FileNotFoundError:
            LOG.info("Стейта нет — первый запуск")
        except (json.JSONDecodeError, OSError, TypeError) as e:
            LOG.error("Стейт %s повреждён (%s) — стартуем с пустого", self.path, e)

    def _push(self, key):
        if key in self.keys:
            return
        self.keys.add(key)
        self.order.append(key)
        while len(self.order) > self.cap:
            self.keys.discard(self.order.popleft())

    def __contains__(self, key):
        return key in self.keys

    def __len__(self):
        return len(self.keys)

    def add(self, key):
        self._push(key)

    def save(self):
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(list(self.order), f, ensure_ascii=False)
            os.replace(tmp, self.path)  # атомарно: обрыв не оставит битый стейт
        except OSError as e:
            LOG.error("Не смог сохранить стейт: %s", e)


class Limiter:
    """Не чаще MIN_POST_INTERVAL между постами и не больше N в минуту."""

    def __init__(self):
        self.sent = deque()

    def wait(self):
        now = time.time()
        while self.sent and now - self.sent[0] > 60:
            self.sent.popleft()
        delays = []
        if self.sent:
            delays.append(MIN_POST_INTERVAL - (now - self.sent[-1]))
        if len(self.sent) >= MAX_POSTS_PER_MINUTE:
            delays.append(self.sent[0] + 60 - now)
        pause = max(delays, default=0)
        if pause > 0:
            time.sleep(pause)

    def mark(self):
        self.sent.append(time.time())


def send(cfg, limiter, text, stop):
    """Отправка в канал. Возвращает True только при подтверждённой доставке."""
    for attempt in range(3):
        if stop.is_set():
            return False
        limiter.wait()
        try:
            r = requests.post(
                API.format(token=cfg["token"]),
                json={
                    "chat_id": cfg["channel"],
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                },
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as e:
            LOG.warning("Сеть при отправке (попытка %d): %s", attempt + 1, e)
            stop.wait(5)
            continue

        if r.status_code == 429:
            retry = 5
            try:
                retry = int(r.json().get("parameters", {}).get("retry_after", 5))
            except ValueError:
                pass
            LOG.warning("429 от Telegram, ждём %d с", retry)
            stop.wait(retry + 1)
            continue

        if r.ok:
            limiter.mark()
            return True

        # 400/403 — проблема с самим сообщением или правами, ретрай не поможет.
        LOG.error("Telegram %d: %s", r.status_code, r.text[:300])
        return False

    return False


def fetch(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        LOG.warning("Лента недоступна %s: %s", url, e)
        return []

    parsed = feedparser.parse(r.content)
    source = parsed.feed.get("title", urllib.parse.urlsplit(url).netloc)
    items = []
    for e in parsed.entries:
        link = (e.get("link") or "").strip()
        title = (e.get("title") or "").strip()
        if not link or not title:
            continue
        ts = e.get("published_parsed") or e.get("updated_parsed")
        items.append({
            "key": normalize(link),
            "link": link,
            "title": title,
            "source": source,
            "summary": extract_summary(e),
            "ts": timegm(ts) if ts else None,
        })
    return items


def strip_html(raw):
    """Текст без тегов и схлопнутыми пробелами."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def extract_summary(entry):
    """Описание записи из ленты: summary или тело content, обрезанное."""
    raw = entry.get("summary", "")
    content = entry.get("content")
    if content and isinstance(content, list) and content[0].get("value"):
        # content обычно полнее summary — берём его, если он длиннее.
        if len(content[0]["value"]) > len(raw):
            raw = content[0]["value"]
    return strip_html(raw)[:1500]


def title_key(title):
    """Второй ключ дедупликации: один источник умеет отдавать статью под
    несколькими URL (у OpenAI /index/... и /business/...), там ссылки не спасают."""
    return "title:" + " ".join(title.lower().split())


def collect(feeds, seen):
    """Новые записи по всем лентам, старые — сразу в seen, чтобы не всплыли."""
    fresh, cutoff = [], time.time() - MAX_AGE_DAYS * 86400
    # Последовательный обход 12 лент не укладывается в минутный интервал.
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = pool.map(fetch, feeds)

    # Одна статья попадает в несколько лент сразу (хабы Хабра пересекаются),
    # и в пределах одного цикла в seen её ещё нет — дубль надо гасить прямо тут.
    taken = set()
    for item in (i for batch in batches for i in batch):
        keys = (item["key"], title_key(item["title"]))
        if item["ts"] is not None and item["ts"] < cutoff:
            seen.add(item["key"])
            continue
        if any(k in seen or k in taken for k in keys):
            continue
        taken.update(keys)
        fresh.append(item)
    # Без даты — считаем самыми старыми: пусть уходят первыми.
    fresh.sort(key=lambda i: (i["ts"] is not None, i["ts"] or 0))
    return fresh


def is_russian(text):
    """Кириллицы больше трети букв — считаем, что переводить нечего."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return True
    cyr = sum("Ѐ" <= c <= "ӿ" for c in letters)
    return cyr / len(letters) > 0.33


def translate(title, cfg):
    """Русский заголовок или None, если перевод не нужен/не удался."""
    if not cfg["ds_key"] or is_russian(title):
        return None
    try:
        r = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {cfg['ds_key']}"},
            json={
                "model": cfg["ds_model"],
                "messages": [
                    {"role": "system", "content": TRANSLATE_PROMPT},
                    {"role": "user", "content": title},
                ],
                "max_tokens": 300,
                "temperature": 0.2,
            },
            timeout=DEEPSEEK_TIMEOUT,
        )
        r.raise_for_status()
        out = r.json()["choices"][0]["message"]["content"].strip().strip('"').rstrip(".")
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        LOG.warning("Перевод не удался (%s), публикую оригинал", e)
        return None
    # Модель иногда отвечает пояснением вместо перевода — такое не берём.
    return out if out and len(out) < 400 and "\n" not in out else None


def describe(item, cfg):
    """(заголовок_ru, описание_ru) по заголовку и summary из ленты.

    Одним вызовом получаем и перевод, и краткий пересказ, чтобы читатель понял
    суть, не переходя на сайт. При сбое — откат на голый перевод заголовка.
    """
    if not cfg["ds_key"]:
        return (item["title"] if is_russian(item["title"]) else None), None
    user_msg = f"Заголовок: {item['title']}\n\nОписание из ленты: {item['summary'] or '(нет)'}"
    try:
        r = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {cfg['ds_key']}"},
            json={
                "model": cfg["ds_model"],
                "messages": [
                    {"role": "system", "content": DESCRIBE_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
                "max_tokens": 600,
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
            timeout=DEEPSEEK_TIMEOUT,
        )
        r.raise_for_status()
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        title_ru = (data.get("title") or "").strip() or None
        summary_ru = (data.get("summary") or "").strip() or None
        takeaway = (data.get("takeaway") or "").strip() or None
        tag = (data.get("tag") or "").strip().lower().lstrip("#")
        tag = tag if tag in CATEGORY_TAGS else None
        hook = (data.get("hook") or "").strip() or None
        howto = (data.get("howto") or "").strip() or None
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        LOG.warning("Описание не удалось (%s), откат на перевод заголовка", e)
        return {"title": translate(item["title"], cfg)}
    return {"title": title_ru, "summary": summary_ru, "takeaway": takeaway,
            "tag": tag, "hook": hook, "howto": howto}


def render(item, desc=None, cfg=None):
    desc = desc or {}
    source = html.escape(item["source"])
    link = html.escape(item["link"], quote=True)
    head = html.escape(desc.get("title") or item["title"])
    parts = [f"<b>{head}</b>"]
    if desc.get("summary"):
        parts.append(html.escape(desc["summary"]))
    if desc.get("howto"):
        parts.append("🛠 " + html.escape(desc["howto"]))
    if desc.get("takeaway"):
        parts.append("💡 " + html.escape(desc["takeaway"]))
    if desc.get("hook"):
        parts.append("❓ " + html.escape(desc["hook"]))
    parts.append(f'<a href="{link}">{source}</a>')

    # Нижняя строка: рубрика + репост + призыв подписаться.
    footer = []
    if desc.get("tag"):
        footer.append(f"#{desc['tag']}")
    if cfg and cfg.get("channel_link"):
        share = "https://t.me/share/url?url=" + urllib.parse.quote(cfg["channel_link"], safe="")
        footer.append(f'<a href="{share}">Переслать другу</a>')
        footer.append(f'<a href="{cfg["channel_link"]}">Что там у ИИ 👀</a>')
    if footer:
        parts.append("   ".join(footer))
    return "\n\n".join(parts)


def gather_candidates(feeds, seen, hours=DIGEST_WINDOW_HOURS, cap=DIGEST_PER_SOURCE_CAP):
    """Непосланные записи за последние `hours` часов.

    С каждой ленты берём не больше `cap` свежих записей, иначе arXiv своими
    сотнями препринтов вытеснит блоги и новостники из выборки.
    """
    cutoff = time.time() - hours * 3600
    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = pool.map(fetch, feeds)

    taken, out = set(), []
    for batch in batches:
        recent = sorted((i for i in batch if i["ts"] and i["ts"] >= cutoff),
                        key=lambda i: i["ts"], reverse=True)[:cap]
        for item in recent:
            keys = (item["key"], title_key(item["title"]))
            if any(k in seen or k in taken for k in keys):
                continue
            taken.update(keys)
            out.append(item)
    out.sort(key=lambda i: i["ts"], reverse=True)
    return out[:DIGEST_MAX_CANDIDATES]


def curate(items, cfg, max_n=DIGEST_MAX_POSTS):
    """Индексы интересных новостей по мнению DeepSeek (по убыванию важности)."""
    if not cfg["ds_key"]:
        LOG.error("Нет ключа DeepSeek — курировать нечем, подборка пропущена")
        return []
    listing = "\n".join(f"{i}. {it['title']}" for i, it in enumerate(items))
    try:
        r = requests.post(
            DEEPSEEK_URL,
            headers={"Authorization": f"Bearer {cfg['ds_key']}"},
            json={
                "model": cfg["ds_model"],
                "messages": [
                    {"role": "system", "content": CURATE_PROMPT},
                    {"role": "system", "content": f"Ограничение сейчас: максимум {max_n} новостей."},
                    {"role": "user", "content": listing},
                ],
                "max_tokens": 400,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
            },
            timeout=CURATE_TIMEOUT,
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as e:
        LOG.error("Курирование не удалось: %s", e)
        return []

    picks = _parse_picks(raw)
    valid, used = [], set()
    for n in picks:
        if isinstance(n, int) and 0 <= n < len(items) and n not in used:
            used.add(n)
            valid.append(n)
    return valid[:max_n]


def _parse_picks(raw):
    """Достаёт список номеров из ответа модели, терпимо к обёрткам."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):] if "{" in text else text
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return [int(x) for x in re.findall(r"\d+", raw)]


def run_digest(cfg):
    """Один утренний прогон: собрать за сутки, отобрать интересное, выложить."""
    with open(cfg["feeds_file"], encoding="utf-8") as f:
        feeds = json.load(f)
    seen = Seen(cfg["state_file"])
    limiter = Limiter()
    stop = threading.Event()

    candidates = gather_candidates(feeds, seen)
    LOG.info("Дайджест: кандидатов за %d ч — %d", DIGEST_WINDOW_HOURS, len(candidates))
    if not candidates:
        LOG.info("Свежих новостей нет — сегодня канал молчит, это нормально")
        return

    picks = curate(candidates, cfg)
    LOG.info("Отобрано интересных: %d из %d", len(picks), len(candidates))
    if not picks:
        LOG.info("Ничего стоящего не набралось — сегодня не постим")
        return

    # Самое важное постим последним, чтобы оно оказалось самым свежим в канале.
    for idx in reversed(picks):
        item = candidates[idx]
        desc = describe(item, cfg)
        if send(cfg, limiter, render(item, desc, cfg), stop):
            seen.add(item["key"])
            seen.add(title_key(item["title"]))
            seen.save()
            LOG.info("→ %s", item["title"][:80])
        else:
            LOG.error("Не отправлено: %s", item["link"])
    LOG.info("Дайджест готов")


def run_daily(cfg):
    """Утренний пост «Главное за день»: одним сообщением топ новостей за сутки.

    Это рекап: берём лучшее за 24 ч НЕЗАВИСИМО от того, что уже вышло почасово,
    и не помечаем seen — чтобы почасовой режим работал сам по себе.
    """
    with open(cfg["feeds_file"], encoding="utf-8") as f:
        feeds = json.load(f)

    # Пустое множество вместо стейта: рекап включает и уже опубликованное.
    candidates = gather_candidates(feeds, set(),
                                   hours=DAILY_WINDOW_HOURS, cap=DAILY_PER_SOURCE_CAP)
    LOG.info("Главное за день: кандидатов за 24 ч — %d", len(candidates))
    if not candidates:
        LOG.info("За сутки новостей нет — утренний пост пропущен")
        return

    picks = curate(candidates, cfg, max_n=DAILY_MAX)
    if not picks:
        LOG.info("Топ за сутки пуст — утренний пост пропущен")
        return

    today = time.strftime("%d.%m")
    lines = [f"🌅 <b>Главное за день · {today}</b>"]
    for n, idx in enumerate(picks, 1):
        item = candidates[idx]
        title_ru = translate(item["title"], cfg) or item["title"]
        link = html.escape(item["link"], quote=True)
        lines.append(f'{n}. <b>{html.escape(title_ru)}</b>\n<a href="{link}">'
                     f'{html.escape(item["source"])}</a>')
    footer = "#дайджест"
    if cfg.get("channel_link"):
        footer += f'   <a href="{cfg["channel_link"]}">Что там у ИИ 👀</a>'
    lines.append(footer)

    if send(cfg, Limiter(), "\n\n".join(lines), threading.Event()):
        LOG.info("Утренний пост «Главное за день» отправлен (%d новостей)", len(picks))
    else:
        LOG.error("Не удалось отправить утренний пост")


def run_weekly(cfg):
    """Воскресный «Топ-5 недели»: якорный пост, который удобно пересылать.

    Рекап за 7 дней: включает уже опубликованное, seen не трогаем.
    """
    with open(cfg["feeds_file"], encoding="utf-8") as f:
        feeds = json.load(f)

    candidates = gather_candidates(feeds, set(),
                                   hours=WEEKLY_WINDOW_HOURS, cap=WEEKLY_PER_SOURCE_CAP)
    LOG.info("Топ-5 недели: кандидатов за 7 дней — %d", len(candidates))
    if not candidates:
        LOG.info("За неделю новостей нет — недельный пост пропущен")
        return

    picks = curate(candidates, cfg, max_n=WEEKLY_MAX)
    if not picks:
        LOG.info("Недельный топ пуст — пост пропущен")
        return

    end = time.strftime("%d.%m")
    start = time.strftime("%d.%m", time.localtime(time.time() - WEEKLY_WINDOW_HOURS * 3600))
    lines = [f"🏆 <b>Топ-{len(picks)} недели · {start}–{end}</b>"]
    for n, idx in enumerate(picks, 1):
        item = candidates[idx]
        title_ru = translate(item["title"], cfg) or item["title"]
        link = html.escape(item["link"], quote=True)
        lines.append(f'{n}. <b>{html.escape(title_ru)}</b>\n<a href="{link}">'
                     f'{html.escape(item["source"])}</a>')
    footer = "#топнедели"
    if cfg.get("channel_link"):
        share = "https://t.me/share/url?url=" + urllib.parse.quote(cfg["channel_link"], safe="")
        footer += f'   <a href="{share}">Переслать другу</a>'
        footer += f'   <a href="{cfg["channel_link"]}">Что там у ИИ 👀</a>'
    lines.append(footer)

    if send(cfg, Limiter(), "\n\n".join(lines), threading.Event()):
        LOG.info("«Топ-5 недели» отправлен (%d новостей)", len(picks))
    else:
        LOG.error("Не удалось отправить недельный пост")


def run(cfg, stop):
    with open(cfg["feeds_file"], encoding="utf-8") as f:
        feeds = json.load(f)
    if not isinstance(feeds, list) or not feeds:
        sys.exit(f"{cfg['feeds_file']}: ожидается непустой JSON-список URL")

    seen = Seen(cfg["state_file"])
    limiter = Limiter()
    priming = len(seen) == 0

    LOG.info(
        "Лент: %d, интервал: %d с, в стейте: %d, перевод: %s",
        len(feeds), cfg["interval"], len(seen),
        cfg["ds_model"] if cfg["ds_key"] else "выключен (нет ключа)",
    )

    while not stop.is_set():
        try:
            fresh = collect(feeds, seen)
        except Exception:
            LOG.exception("Сбой цикла опроса — продолжаем")
            fresh = []

        if priming:
            # Первый запуск: бэклог в канал не льём, только помечаем прочитанным.
            for item in fresh:
                seen.add(item["key"])
                seen.add(title_key(item["title"]))
            seen.save()
            LOG.info("Первый запуск: %d записей помечены прочитанными, не постим", len(fresh))
            priming, fresh = False, []

        if fresh:
            batch = fresh[:MAX_POSTS_PER_CYCLE]
            LOG.info("Новых: %d, постим: %d", len(fresh), len(batch))
            for item in batch:
                if stop.is_set():
                    break
                ru = translate(item["title"], cfg)
                if send(cfg, limiter, render(item, {"title": ru}, cfg), stop):
                    seen.add(item["key"])
                    seen.add(title_key(item["title"]))
                    seen.save()  # после каждого поста: падение не приведёт к дублю
                    LOG.info("→ %s", item["title"][:80])
                else:
                    LOG.error("Не отправлено, повторим в следующем цикле: %s", item["link"])

        stop.wait(cfg["interval"])

    seen.save()
    LOG.info("Остановлен, стейт сохранён (%d ключей)", len(seen))


def main():
    logging.basicConfig(
        stream=sys.stdout,
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cfg = load_config()
    mode = sys.argv[1] if len(sys.argv) > 1 else "watch"
    if mode == "digest":
        run_digest(cfg)
        return
    if mode == "daily":
        run_daily(cfg)
        return
    if mode == "weekly":
        run_weekly(cfg)
        return
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run(cfg, stop)


if __name__ == "__main__":
    main()
