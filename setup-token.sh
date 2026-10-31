#!/usr/bin/env bash
# Спрашивает токен и канал, вписывает их в .env и проверяет связь с Telegram.
set -u
ENV_FILE="$HOME/tg-ai-news/.env"

echo "Вставь токен от @BotFather (Ctrl+Shift+V) и нажми Enter:"
read -r TOKEN
echo "Теперь username канала, например @ai_news_dm, и Enter:"
read -r CHANNEL

# Обрезаем случайные пробелы по краям — самая частая причина 401.
TOKEN="${TOKEN#"${TOKEN%%[![:space:]]*}"}"; TOKEN="${TOKEN%"${TOKEN##*[![:space:]]}"}"
CHANNEL="${CHANNEL#"${CHANNEL%%[![:space:]]*}"}"; CHANNEL="${CHANNEL%"${CHANNEL##*[![:space:]]}"}"

[[ -z "$TOKEN" || -z "$CHANNEL" ]] && { echo "ОШИБКА: пустое значение, ничего не записал."; exit 1; }
[[ "$TOKEN" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]] || {
  echo "ОШИБКА: токен не похож на токен. Ожидается вид 1234567890:AAF... — проверь, что скопировал целиком."
  exit 1
}
[[ "$CHANNEL" == @* || "$CHANNEL" == -100* ]] || {
  echo "ОШИБКА: канал должен начинаться с @ (публичный) или с -100 (числовой ID)."
  exit 1
}

python3 - "$ENV_FILE" "$TOKEN" "$CHANNEL" <<'PY'
import sys
path, token, channel = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding="utf-8").read().splitlines()
out = []
for line in lines:
    if line.startswith("TELEGRAM_BOT_TOKEN="):
        out.append(f"TELEGRAM_BOT_TOKEN={token}")
    elif line.startswith("TELEGRAM_CHANNEL_ID="):
        out.append(f"TELEGRAM_CHANNEL_ID={channel}")
    else:
        out.append(line)
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
chmod 600 "$ENV_FILE"
echo "Записал в $ENV_FILE"

echo
echo "--- Проверка бота:"
GETME=$(curl -s "https://api.telegram.org/bot${TOKEN}/getMe")
if grep -q '"ok":true' <<<"$GETME"; then
  echo "OK, бот: @$(sed -n 's/.*"username":"\([^"]*\)".*/\1/p' <<<"$GETME")"
else
  echo "НЕ ОК. Telegram ответил:"; echo "$GETME"
  echo "Скорее всего токен неверный. Перевыпусти: @BotFather -> /token"
  exit 1
fi

echo "--- Проверка канала (отправлю тестовое сообщение):"
SEND=$(curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHANNEL}" \
  --data-urlencode "text=проверка связи, можно удалить")
if grep -q '"ok":true' <<<"$SEND"; then
  echo "OK. В канале появилось сообщение «проверка связи» — удали его руками."
  echo
  echo "Всё готово. Запуск:  systemctl --user enable --now ai-news-bot"
else
  echo "НЕ ОК. Telegram ответил:"; echo "$SEND"
  case "$SEND" in
    *"chat not found"*)        echo "-> Неверный username канала, либо канал приватный." ;;
    *"not enough rights"*)     echo "-> Бот админ, но без права «Публикация сообщений». Включи его." ;;
    *"bot is not a member"*)   echo "-> Бот не добавлен в администраторы канала." ;;
  esac
  exit 1
fi
