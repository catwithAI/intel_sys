#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Check .env file
if [ ! -f .env ]; then
    echo "Error: .env file not found. Copy .env.example to .env and fill in your config."
    exit 1
fi

# Start uvicorn
exec uvicorn app.main:app --host 0.0.0.0 --port 7777 --reload
