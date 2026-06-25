#!/bin/bash

# Configuration
BACKEND_PORT=7070
FRONTEND_PORT=3000
LOG_DIR="/Users/nithish-prabhu/Downloads/intra-day/logs"
PID_FILE="/Users/nithish-prabhu/Downloads/intra-day/.services.pid"

mkdir -p "$LOG_DIR"

echo "=== Starting ChartEdge AI Services ==="

# Check if Backend is already running
if lsof -i :$BACKEND_PORT > /dev/null 2>&1; then
    echo "⚠️  Backend is already running on port $BACKEND_PORT."
else
    echo "🚀 Starting Backend on port $BACKEND_PORT..."
    # Run backend using virtualenv uvicorn
    # Using --host 127.0.0.1 to avoid macOS firewall prompts
    cd /Users/nithish-prabhu/Downloads/intra-day
    ./.venv/bin/uvicorn services.chartedge_core.api:app --host 127.0.0.1 --port $BACKEND_PORT > "$LOG_DIR/backend.log" 2>&1 &
    BACKEND_PID=$!
    echo $BACKEND_PID > "$PID_FILE"
    echo "Backend started with PID $BACKEND_PID. Logging to $LOG_DIR/backend.log"
fi

# Wait for backend to be healthy
echo "⏳ Waiting for Backend to become healthy..."
for i in {1..30}; do
    if curl -s http://127.0.0.1:$BACKEND_PORT/health | grep -q '"status":"ok"'; then
        echo "✅ Backend is healthy!"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "❌ Backend health check failed after 30 seconds. Check logs at $LOG_DIR/backend.log"
        exit 1
    fi
    sleep 1
done

# Check if Frontend is already running
if lsof -i :$FRONTEND_PORT > /dev/null 2>&1; then
    echo "⚠️  Frontend is already running on port $FRONTEND_PORT."
else
    echo "🚀 Starting Frontend..."
    cd /Users/nithish-prabhu/Downloads/intra-day/frontend
    # Run next dev with backend URL pointing to port 7070
    NEXT_PUBLIC_API_URL=http://127.0.0.1:$BACKEND_PORT npm run dev > "$LOG_DIR/frontend.log" 2>&1 &
    FRONTEND_PID=$!
    echo $FRONTEND_PID >> "$PID_FILE"
    echo "Frontend started with PID $FRONTEND_PID. Logging to $LOG_DIR/frontend.log"
fi

echo "=== Startup sequence complete ==="
echo "Backend: http://127.0.0.1:$BACKEND_PORT"
echo "Frontend: http://127.0.0.1:$FRONTEND_PORT"
