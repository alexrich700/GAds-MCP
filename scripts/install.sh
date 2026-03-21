#!/bin/bash
# ==============================================================================
# GAds-MCP Installer
# Rossman Media - Google Ads MCP Setup
#
# This script installs GAds-MCP (AdLoop) and connects it to Claude.
# Run the pre-flight check first to make sure your machine is ready.
#
# Usage: curl -sSL https://raw.githubusercontent.com/alexrich700/GAds-MCP/main/scripts/install.sh | bash
# ==============================================================================

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

REPO_URL="https://github.com/alexrich700/GAds-MCP.git"
INSTALL_DIR="$HOME/.gads-mcp"
CONFIG_DIR="$HOME/.adloop"
CLAUDE_CONFIG_DIR="$HOME/Library/Application Support/Claude"
CLAUDE_CONFIG_FILE="$CLAUDE_CONFIG_DIR/claude_desktop_config.json"

echo ""
echo "==========================================="
echo "  GAds-MCP Installer"
echo "  Rossman Media"
echo "==========================================="
echo ""

# -------------------------------------------
# 1. Check prerequisites
# -------------------------------------------
echo -e "${BOLD}Step 1/6: Checking prerequisites...${NC}"

# Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: Python 3 not found. Run the pre-flight check first.${NC}"
    exit 1
fi

PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
if [[ "$PY_MINOR" -lt 11 ]]; then
    echo -e "${RED}Error: Python $PY_VERSION found but 3.11+ required. Run the pre-flight check first.${NC}"
    exit 1
fi
echo -e "  ${GREEN}Python $PY_VERSION${NC}"

# Git
if ! command -v git &> /dev/null; then
    echo -e "${RED}Error: Git not found. Run the pre-flight check first.${NC}"
    exit 1
fi
echo -e "  ${GREEN}Git OK${NC}"
echo ""

# -------------------------------------------
# 2. Install uv if needed
# -------------------------------------------
echo -e "${BOLD}Step 2/6: Setting up package manager...${NC}"

if ! command -v uv &> /dev/null; then
    echo "  Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh 2>/dev/null

    # Source the env so uv is available in this session
    if [[ -f "$HOME/.local/bin/env" ]]; then
        source "$HOME/.local/bin/env"
    fi
    # Also add to path directly in case the source above doesn't work
    export PATH="$HOME/.local/bin:$PATH"

    if command -v uv &> /dev/null; then
        echo -e "  ${GREEN}uv installed successfully${NC}"
    else
        echo -e "${RED}Error: uv installation failed. Try manually: curl -LsSf https://astral.sh/uv/install.sh | sh${NC}"
        exit 1
    fi
else
    echo -e "  ${GREEN}uv already installed${NC}"
fi
echo ""

# -------------------------------------------
# 3. Clone or update the repo
# -------------------------------------------
echo -e "${BOLD}Step 3/6: Getting GAds-MCP...${NC}"

if [[ -d "$INSTALL_DIR" ]]; then
    echo "  Found existing installation, updating..."
    cd "$INSTALL_DIR"
    git pull --quiet origin main
    echo -e "  ${GREEN}Updated to latest version${NC}"
else
    echo "  Cloning from GitHub..."
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    echo -e "  ${GREEN}Downloaded${NC}"
fi

cd "$INSTALL_DIR"
echo ""

# -------------------------------------------
# 4. Install Python dependencies
# -------------------------------------------
echo -e "${BOLD}Step 4/6: Installing dependencies...${NC}"

uv sync --quiet 2>/dev/null || uv sync
echo -e "  ${GREEN}Dependencies installed${NC}"
echo ""

# -------------------------------------------
# 5. Run adloop init (OAuth + config)
# -------------------------------------------
echo -e "${BOLD}Step 5/6: Setting up Google Ads connection...${NC}"
echo ""
echo -e "  ${YELLOW}This will open your browser for Google sign-in.${NC}"
echo -e "  ${YELLOW}Sign in with your Google account that has access to the MCC.${NC}"
echo ""
echo -e "  ${BLUE}You'll need the following info (Alex can provide these):${NC}"
echo "    - Google Cloud Project ID"
echo "    - Google Ads Developer Token"
echo "    - OAuth Client ID and Client Secret"
echo ""
read -p "  Ready? Press Enter to continue (or Ctrl+C to exit)... "
echo ""

# Run the init wizard
uv run adloop init

echo ""
echo -e "  ${GREEN}Google Ads connection configured${NC}"
echo ""

# -------------------------------------------
# 6. Configure Claude MCP
# -------------------------------------------
echo -e "${BOLD}Step 6/6: Connecting to Claude...${NC}"

# Get the full path to the Python in the venv
PYTHON_PATH="$INSTALL_DIR/.venv/bin/python"

if [[ ! -f "$PYTHON_PATH" ]]; then
    # Fallback: find the python in the venv
    PYTHON_PATH=$(find "$INSTALL_DIR/.venv" -name "python3" -type f 2>/dev/null | head -1)
fi

if [[ -z "$PYTHON_PATH" || ! -f "$PYTHON_PATH" ]]; then
    echo -e "${RED}Error: Could not find Python in the virtual environment.${NC}"
    echo "  Please contact Alex for help."
    exit 1
fi

# The MCP server entry we need to add
MCP_ENTRY=$(cat <<EOF
{
  "mcpServers": {
    "gads-mcp": {
      "command": "$PYTHON_PATH",
      "args": ["-m", "adloop"]
    }
  }
}
EOF
)

# --- Claude Desktop / Cowork config ---
CLAUDE_CONFIGURED=false

if [[ "$OSTYPE" == "darwin"* ]]; then
    if [[ -d "$CLAUDE_CONFIG_DIR" ]] || [[ -d "/Applications/Claude.app" ]]; then
        echo "  Configuring Claude Desktop / Cowork..."

        # Create config dir if it doesn't exist
        mkdir -p "$CLAUDE_CONFIG_DIR"

        if [[ -f "$CLAUDE_CONFIG_FILE" ]]; then
            # Config file exists, check if gads-mcp is already there
            if grep -q "gads-mcp" "$CLAUDE_CONFIG_FILE" 2>/dev/null; then
                echo -e "  ${GREEN}Claude Desktop already configured${NC}"
                CLAUDE_CONFIGURED=true
            else
                # Need to merge. Use python for safe JSON manipulation.
                python3 << PYEOF
import json
import sys
import shutil

config_file = "$CLAUDE_CONFIG_FILE"
python_path = "$PYTHON_PATH"

try:
    with open(config_file, 'r') as f:
        config = json.load(f)
except (json.JSONDecodeError, FileNotFoundError):
    config = {}

# Create backup
shutil.copy2(config_file, config_file + ".backup")

# Add or update mcpServers
if 'mcpServers' not in config:
    config['mcpServers'] = {}

config['mcpServers']['gads-mcp'] = {
    "command": python_path,
    "args": ["-m", "adloop"]
}

with open(config_file, 'w') as f:
    json.dump(config, f, indent=2)

print("  Config updated (backup saved as claude_desktop_config.json.backup)")
PYEOF
                echo -e "  ${GREEN}Claude Desktop configured${NC}"
                CLAUDE_CONFIGURED=true
            fi
        else
            # No config file yet, create one
            echo "$MCP_ENTRY" > "$CLAUDE_CONFIG_FILE"
            echo -e "  ${GREEN}Claude Desktop config created${NC}"
            CLAUDE_CONFIGURED=true
        fi
    fi
fi

# --- Claude Code config (project-level .mcp.json) ---
echo ""
echo "  For Claude Code, the MCP is configured per-project."
echo "  When you open a project in Claude Code, create a .mcp.json file"
echo "  in the project root with this content:"
echo ""
echo -e "  ${BLUE}$(cat <<EOF
{
  "mcpServers": {
    "gads-mcp": {
      "command": "$PYTHON_PATH",
      "args": ["-m", "adloop"]
    }
  }
}
EOF
)${NC}"
echo ""

# Also save this to a file they can easily copy
MCP_JSON_FILE="$INSTALL_DIR/mcp-config-snippet.json"
cat > "$MCP_JSON_FILE" <<EOF
{
  "mcpServers": {
    "gads-mcp": {
      "command": "$PYTHON_PATH",
      "args": ["-m", "adloop"]
    }
  }
}
EOF

echo "  This snippet is also saved to: $MCP_JSON_FILE"
echo "  You can copy it anytime with: cat $MCP_JSON_FILE"
echo ""

# -------------------------------------------
# Done!
# -------------------------------------------
echo "==========================================="
echo ""
echo -e "  ${GREEN}${BOLD}Setup complete!${NC}"
echo ""

if [[ "$CLAUDE_CONFIGURED" == true ]]; then
    echo -e "  ${GREEN}Claude Desktop / Cowork:${NC} Configured"
    echo "    Restart Claude Desktop for changes to take effect."
    echo ""
fi

echo -e "  ${BOLD}To verify it's working:${NC}"
echo "    1. Open Claude Desktop (or restart it if it was open)"
echo "    2. Start a new conversation"
echo '    3. Ask: "List my Google Ads accounts"'
echo "    4. Claude should call the gads-mcp tool and show your accounts"
echo ""
echo -e "  ${BOLD}To update later:${NC}"
echo "    cd $INSTALL_DIR && git pull && uv sync"
echo ""
echo -e "  ${BOLD}If something breaks:${NC}"
echo "    Screenshot the error and send it to Alex."
echo ""
echo -e "  ${BOLD}Config locations:${NC}"
echo "    GAds-MCP install: $INSTALL_DIR"
echo "    AdLoop config:    $CONFIG_DIR/config.yaml"
echo "    Claude config:    $CLAUDE_CONFIG_FILE"
echo "    MCP snippet:      $MCP_JSON_FILE"
echo ""
echo "==========================================="
echo ""
