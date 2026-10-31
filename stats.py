#!/usr/bin/env python3
"""Метрики канала «Что там у AI»: подписчики, просмотры, дневник роста.

Замеры складываются в stats.json — по ним видно, работают ли изменения
контента и дистрибуции, и считается эффективность любых закупок.

Использование:
    python stats.py snapshot      # снять замер и дописать в stats.json
    python stats.py report [дней] # динамика за период (по умолчанию 7)
"""

import json
import os
import re
import sys
import time
from datetime import date

import requests

UA = "Mozilla/5.0 (compatible; ai-news-bot/1.0)"
HTTP_TIMEOUT = 15
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stats.json")
VIEWS_SAMPLE = 30  # сколько последних постов анализируем


def channel_slug():
    raw = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    return raw.lstrip("@") if raw.startswith("@") else raw


def api(method, **params):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return None
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/{method}",
                         params=params, timeout=HTTP_TIMEOUT)
        data = r.json()
    except (requests.RequestException, ValueError):
        return None
    return data.get("result") if data.get("ok") else None


def parse_count(text):
    """'1,2K' / '12K' / '345' → число."""
    s = text.strip().lower().replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    mult = 1
    if s.endswith(("k", "к")):
        mult, s = 1000, s[:-1]
    elif s.endswith(("m", "м")):
        mult, s = 1000000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def page_metrics(slug):
    """Подписчики и просмотры последних постов с публичной страницы t.me/s/<slug>."""
    try:
        r = requests.get(f"https://t.me/s/{slug}", headers={"User-Agent": UA},
                         timeout=HTTP_TIMEOUT)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"Страница канала недоступна: {e}", file=sys.stderr)
        return None, []

    subs = None
    m = re.search(r"([\d\s,.\u00a0]+[KkMmКкМм]?)\s*(?:subscribers|подписчик)", r.text)
    if m:
        subs = parse_count(m.group(1))

    views = [v for v in (parse_count(x) for x in re.findall(
        r"tgme_widget_message_views[^>]*>([^<]+)<", r.text)) if v is not None]
    return subs, views[-VIEWS_SAMPLE:]


def subscribers():
    """Через Bot API (бот — админ канала), иначе — с публичной страницы."""
    n = api("getChatSubscriberCount", chat_id=os.environ.get("TELEGRAM_CHANNEL_ID", ""))
    if isinstance(n, int):
        return n
    subs, _ = page_metrics(channel_slug())
    return subs


def snapshot():
    slug = channel_slug()
    if not slug:
        sys.exit("Не задан TELEGRAM_CHANNEL_ID")
    subs, views = page_metrics(slug)
    if subs is None:
        subs = subscribers()
    record = {
        "ts": int(time.time()),
        "date": date.today().isoformat(),
        "subscribers": subs,
        "posts": len(views),
        "avg_views": round(sum(views) / len(views), 1) if views else None,
        "max_views": max(views) if views else None,
        "min_views": min(views) if views else None,
    }
    history = load()
    history.append(record)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    print(json.dumps(record, ensure_ascii=False))
    return record


def load():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def report(days=7):
    history = load()
    if not history:
        print("Замеров ещё нет — запустите: python stats.py snapshot")
        return
    cutoff = time.time() - days * 86400
    rows = [r for r in history if r.get("ts", 0) >= cutoff] or history[-1:]
    print(f"{'дата':<12} {'подписчики':>10} {'Δ':>6} {'сред.просм.':>11} {'макс':>6}")
    prev = None
    for r in rows:
        delta = "" if prev is None or r.get("subscribers") is None else \
            f"{r['subscribers'] - prev:+d}"
        prev = r.get("subscribers")
        print(f"{r.get('date', '?'):<12} {str(r.get('subscribers')):>10} {delta:>6} "
              f"{str(r.get('avg_views')):>11} {str(r.get('max_views')):>6}")
    first, last = rows[0], rows[-1]
    if first.get("subscribers") is not None and last.get("subscribers") is not None:
        print(f"\nРост за период: {last['subscribers'] - first['subscribers']:+d} "
              f"({first['subscribers']} → {last['subscribers']})")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "report"
    if cmd == "snapshot":
        snapshot()
    elif cmd == "report":
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        report(days)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
