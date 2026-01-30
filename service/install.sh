#!/bin/bash
# Install botbaki as a systemd service
# Run this on the target server after cloning the repo

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

echo "Installing botbaki service..."

# Check for required environment
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "Warning: ANTHROPIC_API_KEY not set"
    echo "You'll need to set it in the service file or environment"
fi

# Check gh is installed and authenticated
if ! command -v gh &> /dev/null; then
    echo "Error: gh CLI not found. Install from https://cli.github.com"
    exit 1
fi

if ! gh auth status &> /dev/null; then
    echo "Error: gh CLI not authenticated. Run 'gh auth login' first"
    exit 1
fi

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install --user -r "$REPO_DIR/requirements.txt"

# Initialize database
echo "Initializing database..."
mkdir -p "$REPO_DIR/data"
sqlite3 "$REPO_DIR/data/botbaki.db" < "$REPO_DIR/schema.sql"

# Copy and customize service file
SERVICE_FILE="$SCRIPT_DIR/botbaki.service"
TARGET_SERVICE="/etc/systemd/system/botbaki.service"

echo "Customizing service file..."
# Update paths for current user and location
sed -e "s|User=kim|User=$USER|g" \
    -e "s|/home/kim/projects/botbaki|$REPO_DIR|g" \
    "$SERVICE_FILE" > /tmp/botbaki.service

echo "Installing service file (requires sudo)..."
sudo cp /tmp/botbaki.service "$TARGET_SERVICE"
rm /tmp/botbaki.service

# Set up environment file for secrets
ENV_DIR="$HOME/.config/botbaki"
ENV_FILE="$ENV_DIR/env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating environment file..."
    mkdir -p "$ENV_DIR"
    echo "ANTHROPIC_API_KEY=" > "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "Edit $ENV_FILE to add your ANTHROPIC_API_KEY"
fi

# Update service to use environment file
sudo sed -i "s|Environment=ANTHROPIC_API_KEY=|# Environment loaded from file\nEnvironmentFile=$ENV_FILE|" "$TARGET_SERVICE"

# Reload systemd
echo "Reloading systemd..."
sudo systemctl daemon-reload

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "  1. Edit $ENV_FILE and add your ANTHROPIC_API_KEY"
echo "  2. Start the service: sudo systemctl start botbaki"
echo "  3. Enable on boot: sudo systemctl enable botbaki"
echo "  4. Check status: sudo systemctl status botbaki"
echo "  5. View logs: journalctl -u botbaki -f"
