#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────
# CodeSheriff – Start all services
# Usage:  bash run_all.sh
#
# Starts the three FastAPI microservices and the Flask dashboard in the
# background, writing each service's stdout/stderr to its own log file.
# Press Ctrl-C to stop all services.
# ─────────────────────────────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if present
if [ -f .env ]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' .env | xargs)
  echo "✅  Loaded .env"
fi

echo "🚀  Starting CodeSheriff services…"

# ── Webhook Service (port 8001) ──────────────────────────────────────────
python -m uvicorn webhook_service.main:app \
  --host 0.0.0.0 --port 8001 \
  > logs/webhook.log 2>&1 &
WEBHOOK_PID=$!
echo "   webhook-service  PID=$WEBHOOK_PID  → http://localhost:8001"

# ── Analysis Service (port 8002) ─────────────────────────────────────────
python -m uvicorn analysis_service.main:app \
  --host 0.0.0.0 --port 8002 \
  > logs/analysis.log 2>&1 &
ANALYSIS_PID=$!
echo "   analysis-service PID=$ANALYSIS_PID  → http://localhost:8002"

# ── Comment Service (port 8003) ──────────────────────────────────────────
python -m uvicorn comment_service.main:app \
  --host 0.0.0.0 --port 8003 \
  > logs/comment.log 2>&1 &
COMMENT_PID=$!
echo "   comment-service  PID=$COMMENT_PID  → http://localhost:8003"

# ── Dashboard (port 8004) ────────────────────────────────────────────────
python dashboard/app.py \
  > logs/dashboard.log 2>&1 &
DASHBOARD_PID=$!
echo "   dashboard        PID=$DASHBOARD_PID  → http://localhost:8004"

echo ""
echo "🤠  CodeSheriff is running!  Press Ctrl-C to stop all services."
echo "    Logs are in ./logs/"

# Cleanup on exit
trap 'echo ""; echo "🛑  Stopping…"; kill $WEBHOOK_PID $ANALYSIS_PID $COMMENT_PID $DASHBOARD_PID 2>/dev/null; exit 0' INT TERM

# Wait for all background jobs
wait
