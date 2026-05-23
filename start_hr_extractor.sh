#!/bin/bash
# HR Recruitment Extractor - Quick Start Script

echo "🚀 HR Recruitment Extractor - Starting..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is not installed. Please install Python 3.9+ first."
    exit 1
fi

echo "✓ Python 3 found: $(python3 --version)"

# Check if required packages are installed
echo ""
echo "📦 Checking dependencies..."

# Create a minimal venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate venv
source .venv/bin/activate
echo "✓ Virtual environment activated"

# Install/upgrade requirements
echo ""
echo "📥 Installing requirements..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

# Install Playwright browsers
echo ""
echo "🌐 Setting up browser automation..."
python3 -m playwright install chromium

# Check if ports are available
echo ""
echo "🔍 Checking port availability..."

# Function to check if port is in use
check_port() {
    if lsof -Pi :$1 -sTCP:LISTEN -t >/dev/null; then
        return 0  # Port in use
    else
        return 1  # Port available
    fi
}

if check_port 8501; then
    echo "⚠️  Port 8501 is in use — lead-extractor may already be running (that's fine)"
fi

if check_port 8001; then
    echo "⚠️  Port 8001 is already in use — killing existing HR automation server..."
    lsof -ti:8001 | xargs kill -9 2>/dev/null || true
    sleep 1
fi

if check_port 8502; then
    echo "⚠️  Port 8502 is already in use — killing existing HR UI..."
    lsof -ti:8502 | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# Start automation server on port 8001 (lead-extractor uses 8000)
echo ""
echo "🤖 Starting HR Automation Server on port 8001..."
python3 -m uvicorn app.server.automation_server:app --host 0.0.0.0 --port 8001 > /tmp/hr_automation_server.log 2>&1 &
AUTOMATION_PID=$!
echo "   PID: $AUTOMATION_PID — logs: /tmp/hr_automation_server.log"

# Wait for automation server to be ready
echo "   Waiting for automation server..."
for i in $(seq 1 10); do
    if curl -s http://localhost:8001/ >/dev/null 2>&1; then
        echo "   ✓ Automation server ready"
        break
    fi
    sleep 1
done

# Start the app
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎯 Starting HR Recruitment Extractor UI..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  UI:               http://localhost:8502"
echo "  Automation API:   http://localhost:8001"
echo ""
echo "  Lead Extractor uses: UI=8501  API=8000  (no conflict)"
echo ""
echo "Press Ctrl+C to stop both servers"
echo ""

# Kill automation server when this script exits
trap "kill $AUTOMATION_PID 2>/dev/null; echo 'Servers stopped.'" EXIT INT TERM

# Run streamlit (foreground — keeps script alive)
streamlit run app/main.py --server.port 8502 --logger.level=info

# Deactivate venv on exit
deactivate
