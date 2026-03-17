#!/usr/bin/env bash
# Daily runner for pm_agent.py
# Scheduled via cron or systemd — see README for setup instructions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "[$(date)] Starting pm_agent..." >> "$SCRIPT_DIR/pm_agent.log"
python pm_agent.py >> "$SCRIPT_DIR/pm_agent.log" 2>&1
echo "[$(date)] pm_agent finished." >> "$SCRIPT_DIR/pm_agent.log"
