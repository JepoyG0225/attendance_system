#!/bin/bash
echo "========================================="
echo "  Saint Joseph Academy"
echo "  Student Attendance System"
echo "========================================="
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "ERROR: Python 3 is not installed."
  echo "Install it from https://python.org or run: brew install python"
  exit 1
fi

cd "$(dirname "$0")"

# Create venv if missing
if [ ! -d ".venv" ]; then
  echo "Setting up virtual environment..."
  python3 -m venv .venv
  source .venv/bin/activate
  echo "Installing dependencies..."
  pip install -r requirements.txt
else
  source .venv/bin/activate
fi

echo ""
echo "Starting server..."
echo "Open your browser at: http://localhost:8000"
echo "Scanner 1:            http://localhost:8000/scanner?id=1&label=Entrance"
echo "Scanner 2:            http://localhost:8000/scanner?id=2&label=Exit"
echo ""
echo "Press Ctrl+C to stop."
echo ""

python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
