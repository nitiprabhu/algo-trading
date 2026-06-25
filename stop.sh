#!/bin/bash

# Configuration
BACKEND_PORT=7070
FRONTEND_PORT=3000
PID_FILE="/Users/nithish-prabhu/Downloads/intra-day/.services.pid"

echo "=== Stopping ChartEdge AI Services ==="

# Read PIDs and kill them if PID file exists
if [ -f "$PID_FILE" ]; then
    echo "Stopping processes recorded in PID file..."
    while read -r pid; do
        if kill -0 "$pid" 2>/dev/null; then
            echo "Killing process $pid..."
            kill "$pid"
        fi
    done < "$PID_FILE"
    rm "$PID_FILE"
else
    echo "No PID file found. Checking ports..."
fi

# Fallback/Safety Check: Kill any remaining processes listening on the configured ports
for PORT in $BACKEND_PORT $FRONTEND_PORT; do
    PIDS=$(lsof -t -i :$PORT)
    if [ -n "$PIDS" ]; then
        echo "Found processes listening on port $PORT: $PIDS. Terminating..."
        kill $PIDS 2>/dev/null
        sleep 1
        # Force kill if still running
        PIDS_FORCE=$(lsof -t -i :$PORT)
        if [ -n "$PIDS_FORCE" ]; then
            echo "Force killing remaining processes on port $PORT: $PIDS_FORCE"
            kill -9 $PIDS_FORCE 2>/dev/null
        fi
    fi
done

echo "✅ All services stopped."
