#!/bin/bash
# Start FastAPI in background, Streamlit in foreground (for cloud deployment)
set -e

echo "Starting automation server on port 8001..."
uvicorn app.server.automation_server:app --host 0.0.0.0 --port 8001 &
UVICORN_PID=$!

echo "Waiting for API to be ready..."
sleep 5

PORT=${PORT:-8501}
echo "Starting Streamlit on port $PORT..."
exec streamlit run app/main.py \
  --server.port $PORT \
  --server.address 0.0.0.0 \
  --server.headless true \
  --browser.gatherUsageStats false
