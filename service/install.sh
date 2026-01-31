#!/bin/bash
# Install botbaki as a systemd service
# Run this on the target server after cloning the repo

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

echo "Installing botbaki service..."

# Check for required credentials
CONFIG_DIR="$HOME/.config/botbaki"
if [ ! -f "$CONFIG_DIR/github-app-id" ]; then
    echo "Error: Missing $CONFIG_DIR/github-app-id"
    echo "Create GitHub App and add credentials first. See README.md"
    exit 1
fi

if [ ! -f "$CONFIG_DIR/github-app-key.pem" ]; then
    echo "Error: Missing $CONFIG_DIR/github-app-key.pem"
    echo "Download private key from GitHub App settings"
    exit 1
fi

if [ ! -f "$CONFIG_DIR/github-installation-id" ]; then
    echo "Error: Missing $CONFIG_DIR/github-installation-id"
    echo "Install the app on your repo and note the installation ID"
    exit 1
fi

# Create venv and install dependencies
echo "Setting up Python environment..."
cd "$REPO_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Initialize database directory
echo "Initializing data directory..."
mkdir -p "$REPO_DIR/data"

# Set up environment file for Anthropic API key
ENV_FILE="$CONFIG_DIR/env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating environment file..."
    echo "ANTHROPIC_API_KEY=" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Edit $ENV_FILE to add your ANTHROPIC_API_KEY"
fi

# Copy and customize service file
SERVICE_FILE="$SCRIPT_DIR/botbaki.service"
TARGET_SERVICE="/etc/systemd/system/botbaki.service"

echo "Customizing service file..."
# Update paths for current user and location
sed -e "s|User=kim|User=$USER|g" \
    -e "s|/home/kim/projects/botbaki|$REPO_DIR|g" \
    -e "s|/home/kim/.config/botbaki|$CONFIG_DIR|g" \
    "$SERVICE_FILE" > /tmp/botbaki.service

echo "Installing service file (requires sudo)..."
sudo cp /tmp/botbaki.service "$TARGET_SERVICE"
rm /tmp/botbaki.service

# Reload systemd
echo "Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Installation complete!"
echo ""
echo "Credentials found in $CONFIG_DIR:"
ls -la "$CONFIG_DIR"
echo ""
echo "Next steps:"
echo "  1. Verify ANTHROPIC_API_KEY is set in $ENV_FILE"
echo "  2. Start the service: sudo systemctl start botbaki"
echo "  3. Enable on boot: sudo systemctl enable botbaki"
echo "  4. Check status: sudo systemctl status botbaki"
echo "  5. View logs: journalctl -u botbaki -f"
