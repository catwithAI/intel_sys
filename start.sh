#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Check .env file
if [ ! -f .env ]; then
    echo "Error: .env file not found. Copy .env.example to .env and fill in your config."
    exit 1
fi

# Logs: tee to a dated file under logs/ so transient delivery failures and
# scheduler activity can be retraced after the fact (stdout-only used to be a
# blind spot for "silently dropped daily push" investigations).
mkdir -p logs
LOG_FILE="logs/intel_sys_$(date +%Y%m%d).log"
echo "Logging to $LOG_FILE"

# Start uvicorn (stdout+stderr to terminal AND log file)
exec uvicorn app.main:app --host 0.0.0.0 --port 7777 --reload 2>&1 | tee -a "$LOG_FILE"
