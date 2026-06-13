#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

mkdir -p logs

python -m uvicorn webhook_service.main:app --host 0.0.0.0 --port 8001 > logs/webhook.log 2>&1 &
python -m uvicorn analysis_service.main:app --host 0.0.0.0 --port 8002 > logs/analysis.log 2>&1 &
python -m uvicorn comment_service.main:app --host 0.0.0.0 --port 8003 > logs/comment.log 2>&1 &
python dashboard/app.py > logs/dashboard.log 2>&1 &

echo "CodeSheriff running on ports 8001-8004 (logs in ./logs/)"
echo "Press Ctrl-C to stop."

trap 'kill $(jobs -p) 2>/dev/null; exit 0' INT TERM
wait
