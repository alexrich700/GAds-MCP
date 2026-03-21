#!/bin/bash
# ==============================================================================
# GAds-MCP Pre-Flight Check
# Rossman Media - Google Ads MCP Setup
#
# Run this first. It checks your machine and tells you what (if anything)
# needs to be fixed before running the installer.
#
# Usage: curl -sSL https://raw.githubusercontent.com/alexrich700/GAds-MCP/main/scripts/preflight.sh | bash
# ==============================================================================

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

PASS=0
FAIL=0
WARN=0

echo ""
echo "==========================================="
echo "  GAds-MCP Pre-Flight Check"
echo "  Rossman Media"
echo "==========================================="
echo ""

check_pass() {
    echo -e "  ${GREEN}[PASS]${NC} $1"
    PASS=$((PASS + 1))
}

check_fail() {
    echo -e "  ${RED}[FAIL]${NC} $1"
    FAIL=$((FAIL + 1))
}

check_warn() {
    echo -e "  ${YELLOW}[WARN]${NC} $1"
    WARN=$((WARN + 1))
}

# -------------------------------------------
# 1. Operating System
# -------------------------------------------
echo "Checking operating system..."
if [[ "$OSTYPE" == "darwin"* ]]; then
    check_pass "macOS detected ($(sw_vers -productVersion))"
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
    check_pass "Linux detected"
else
    check_warn "Detected OS: $OSTYPE - this setup is tested on macOS and Linux"
fi
echo ""

# -------------------------------------------
# 2. Homebrew (macOS only)
# -------------------------------------------
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "Checking Homebrew..."
    if command -v brew &> /dev/null; then
        check_pass "Homebrew installed ($(brew --version | head -1))"
    else
        check_fail "Homebrew not installed"
        echo ""
        echo -e "         ${BLUE}Fix: Run this command, then re-run the pre-flight check:${NC}"
        echo ""
        echo '         /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
        echo ""
    fi
    echo ""
fi

# -------------------------------------------
# 3. Python 3.11+
# -------------------------------------------
echo "Checking Python..."
if command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

    if [[ "$PY_MAJOR" -ge 3 ]] && [[ "$PY_MINOR" -ge 11 ]]; then
        check_pass "Python $PY_VERSION (3.11+ required)"
    else
        check_fail "Python $PY_VERSION found, but 3.11+ is required"
        echo ""
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo -e "         ${BLUE}Fix: Run this command, then re-run the pre-flight check:${NC}"
            echo ""
            echo "         brew install python@3.12"
            echo ""
        else
            echo -e "         ${BLUE}Fix: Install Python 3.12 from https://www.python.org/downloads/${NC}"
            echo ""
        fi
    fi
else
    check_fail "Python 3 not found"
    echo ""
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo -e "         ${BLUE}Fix: Run this command, then re-run the pre-flight check:${NC}"
        echo ""
        echo "         brew install python@3.12"
        echo ""
    else
        echo -e "         ${BLUE}Fix: Install Python 3.12 from https://www.python.org/downloads/${NC}"
        echo ""
    fi
fi
echo ""

# -------------------------------------------
# 4. Git
# -------------------------------------------
echo "Checking Git..."
if command -v git &> /dev/null; then
    check_pass "Git installed ($(git --version))"
else
    check_fail "Git not found"
    echo ""
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo -e "         ${BLUE}Fix: Run: xcode-select --install${NC}"
    else
        echo -e "         ${BLUE}Fix: Install Git from https://git-scm.com/${NC}"
    fi
fi
echo ""

# -------------------------------------------
# 5. uv (Python package manager)
# -------------------------------------------
echo "Checking uv..."
if command -v uv &> /dev/null; then
    check_pass "uv installed ($(uv --version))"
else
    check_warn "uv not installed (the installer will handle this automatically)"
fi
echo ""

# -------------------------------------------
# 6. Claude Desktop / Cowork
# -------------------------------------------
echo "Checking Claude Desktop..."
CLAUDE_CONFIG_DIR="$HOME/Library/Application Support/Claude"
CLAUDE_CONFIG_FILE="$CLAUDE_CONFIG_DIR/claude_desktop_config.json"

if [[ "$OSTYPE" == "darwin"* ]]; then
    if [[ -d "$CLAUDE_CONFIG_DIR" ]]; then
        check_pass "Claude Desktop config directory found"
        if [[ -f "$CLAUDE_CONFIG_FILE" ]]; then
            check_pass "Claude Desktop config file exists"
        else
            check_warn "Claude Desktop config file not found (installer will create it)"
        fi
    else
        check_warn "Claude Desktop config directory not found"
        echo -e "         This is fine if you only use Claude Code (not Claude Desktop/Cowork)"
        echo -e "         If you want Claude Desktop support, install it from https://claude.ai/download"
    fi
else
    check_warn "Claude Desktop config check skipped (non-macOS)"
fi
echo ""

# -------------------------------------------
# 7. Disk space
# -------------------------------------------
echo "Checking disk space..."
if [[ "$OSTYPE" == "darwin"* ]]; then
    AVAILABLE_GB=$(df -g "$HOME" | tail -1 | awk '{print $4}')
else
    AVAILABLE_GB=$(df -BG "$HOME" | tail -1 | awk '{print $4}' | tr -d 'G')
fi

if [[ "$AVAILABLE_GB" -ge 2 ]]; then
    check_pass "${AVAILABLE_GB}GB available (need ~500MB)"
else
    check_warn "Only ${AVAILABLE_GB}GB available, need at least 500MB"
fi
echo ""

# -------------------------------------------
# 8. Network connectivity
# -------------------------------------------
echo "Checking network..."
if curl -sSf https://github.com > /dev/null 2>&1; then
    check_pass "Can reach GitHub"
else
    check_fail "Cannot reach GitHub - check your internet connection or VPN"
fi

if curl -sSf https://pypi.org > /dev/null 2>&1; then
    check_pass "Can reach PyPI (Python packages)"
else
    check_fail "Cannot reach PyPI - check your internet connection or VPN"
fi
echo ""

# -------------------------------------------
# Summary
# -------------------------------------------
echo "==========================================="
echo "  Results"
echo "==========================================="
echo ""
echo -e "  ${GREEN}Passed: $PASS${NC}    ${RED}Failed: $FAIL${NC}    ${YELLOW}Warnings: $WARN${NC}"
echo ""

if [[ $FAIL -eq 0 ]]; then
    echo -e "  ${GREEN}You're good to go!${NC}"
    echo ""
    echo "  Next step: Run the installer:"
    echo ""
    echo "  curl -sSL https://raw.githubusercontent.com/alexrich700/GAds-MCP/main/scripts/install.sh | bash"
    echo ""
else
    echo -e "  ${RED}Fix the failures above, then run this pre-flight check again.${NC}"
    echo ""
    echo "  If you're stuck, screenshot this output and send it to Alex."
    echo ""
fi
