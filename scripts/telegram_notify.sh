#!/bin/bash
# telegram_notify.sh — Send a quick Telegram notification
# Usage: bash telegram_notify.sh "mensaje"
# Reads bot token and admin chat_id from config

CONFIG="/home/jmfraga/voice_assistant/config.yaml"
MESSAGE="${1:-Sin mensaje}"

# Extract bot token (simple grep, no yaml parser needed)
TOKEN=$(grep "bot_token:" "$CONFIG" | head -1 | sed 's/.*: *"*\([^"]*\)"*/\1/' | tr -d ' ')

if [ -z "$TOKEN" ] || [ "$TOKEN" = "TELEGRAM_BOT_TOKEN" ]; then
    exit 0
fi

# Send to first configured contact
CHAT_ID=$(grep -A5 "contacts:" "$CONFIG" | grep -oP '\d{5,}' | head -1)

if [ -n "$CHAT_ID" ]; then
    curl -s -X POST "https://api.telegram.org/bot${TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}" \
        -d "text=🔧 Maya: ${MESSAGE}" \
        > /dev/null 2>&1
fi
