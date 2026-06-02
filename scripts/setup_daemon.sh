#!/bin/bash
# Setup script for AilyChaos Daemon on macOS

set -e

echo "🔧 Setting up AilyChaos Daemon..."

# Create chaos folder
CHAOS_FOLDER="$HOME/aily_chaos"
mkdir -p "$CHAOS_FOLDER"
mkdir -p "$CHAOS_FOLDER/.processed"
mkdir -p "$CHAOS_FOLDER/.failed"

echo "✅ Created chaos folder: $CHAOS_FOLDER"

# Check if running from the right directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "📁 Project root: $PROJECT_ROOT"

# Find Python
if command -v python3 &> /dev/null; then
    PYTHON_PATH=$(which python3)
elif command -v /usr/local/bin/python3 &> /dev/null; then
    PYTHON_PATH="/usr/local/bin/python3"
elif command -v /opt/homebrew/bin/python3 &> /dev/null; then
    PYTHON_PATH="/opt/homebrew/bin/python3"
else
    echo "❌ Python3 not found. Please install Python 3.10+"
    exit 1
fi

echo "🐍 Using Python: $PYTHON_PATH"

# Load environment variables from .env file
ENV_FILE="$PROJECT_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    echo "📋 Loading environment from $ENV_FILE"
    # Export all variables from .env
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "⚠️  No .env file found at $ENV_FILE"
fi

# Check for required API key
if [ -z "$LLM_API_KEY" ] && [ -z "$KIMI_API_KEY" ] && [ -z "$MOONSHOT_API_KEY" ]; then
    echo "❌ Error: LLM_API_KEY, KIMI_API_KEY, or MOONSHOT_API_KEY must be set in .env file"
    echo "   Please add your API key to $ENV_FILE"
    exit 1
fi

# Normalize to KIMI_API_KEY for the daemon environment.
if [ -z "$KIMI_API_KEY" ] && [ -n "$MOONSHOT_API_KEY" ]; then
    export KIMI_API_KEY="$MOONSHOT_API_KEY"
fi
if [ -z "$KIMI_API_KEY" ] && [ -n "$LLM_API_KEY" ]; then
    export KIMI_API_KEY="$LLM_API_KEY"
fi

echo "🔑 API Key configured"

# Check dependencies
echo "📦 Checking dependencies..."
$PYTHON_PATH -c "import aily" 2>/dev/null || {
    echo "⚠️  Aily package not in Python path. Installing..."
    cd "$PROJECT_ROOT"
    pip install -e .
}

# Copy plist file
PLIST_SRC="$SCRIPT_DIR/com.aily.chaos.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.aily.chaos.plist"

# Update plist with correct paths and API key
sed -e "s|/usr/local/bin/python3|$PYTHON_PATH|g" \
    -e "s|/Users/luzi/code/aily|$PROJECT_ROOT|g" \
    "$PLIST_SRC" > "$PLIST_DST"

# Add API key to plist if available
if [ -n "$KIMI_API_KEY" ]; then
    # Insert API key after PYTHONPATH line
    sed -i '' "/PYTHONPATH/a\\
        <key>KIMI_API_KEY</key>\\
        <string>$KIMI_API_KEY</string>" "$PLIST_DST"
fi

echo "✅ Installed launchd plist: $PLIST_DST"

# Load the service
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "✅ Loaded launchd service"

# Start the daemon
echo "🚀 Starting daemon..."
sleep 1
launchctl start com.aily.chaos

echo ""
echo "🎉 AilyChaos Daemon setup complete!"
echo ""
echo "📋 Commands:"
echo "  Check status: launchctl list | grep com.aily.chaos"
echo "  Stop daemon:  launchctl stop com.aily.chaos"
echo "  Start daemon: launchctl start com.aily.chaos"
echo "  View logs:    tail -f ~/aily_chaos/daemon.log"
echo "  View errors:  tail -f ~/aily_chaos/daemon.error.log"
echo ""
echo "📂 Drop files into: $CHAOS_FOLDER"
echo "   - PDFs, images, videos, text files"
echo "   - They will be processed automatically"
echo ""
