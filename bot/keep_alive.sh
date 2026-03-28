#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# keep_alive.sh — Keeps the Telegram bot running forever
#
# Usage:
#   chmod +x keep_alive.sh
#   ./keep_alive.sh
#
# What it does:
#   - Runs the bot
#   - If the bot crashes for ANY reason, waits 10 seconds and restarts it
#   - Logs everything to bot.log with timestamps
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="$SCRIPT_DIR/bot.log"
PYTHON="python3"

echo "🚀 Starting bot keep-alive loop..."
echo "📝 Logs: $LOG_FILE"
echo "Press Ctrl+C to stop."
echo ""

while true; do
    echo "$(date '+%Y-%m-%d %H:%M:%S') [KEEP-ALIVE] Starting bot..." | tee -a "$LOG_FILE"
    cd "$SCRIPT_DIR"
    $PYTHON -u main.py 2>&1 | tee -a "$LOG_FILE"
    EXIT_CODE=$?
    echo "$(date '+%Y-%m-%d %H:%M:%S') [KEEP-ALIVE] Bot exited with code $EXIT_CODE. Restarting in 10s..." | tee -a "$LOG_FILE"
    sleep 10
done
