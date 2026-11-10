#!/usr/bin/env bash
# Install job-tracker on Ubuntu (22.04 or newer).
#
#   ./scripts/install-ubuntu.sh            install dependencies, venv and .env
#   ./scripts/install-ubuntu.sh --service  also install and start a systemd user service
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$REPO_DIR/.venv"
WANT_SERVICE=0
[[ "${1:-}" == "--service" ]] && WANT_SERVICE=1

if [[ $EUID -eq 0 ]]; then
    echo "Run this as your normal user, not root. It calls sudo only for apt." >&2
    exit 1
fi

# Python 3.11+ is required. Ubuntu 24.04 ships 3.12; 22.04 ships 3.10 but has python3.11 in universe.
pick_python() {
    for py in python3 python3.13 python3.12 python3.11; do
        if command -v "$py" >/dev/null 2>&1 &&
            "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
            echo "$py"
            return 0
        fi
    done
    return 1
}

PYTHON="$(pick_python || true)"
if [[ -z "$PYTHON" ]]; then
    echo "Python 3.11+ not found, installing python3.11 with apt..."
    sudo apt-get update
    sudo apt-get install -y python3.11 python3.11-venv
    PYTHON=python3.11
fi

# The venv module is a separate apt package on Ubuntu.
if ! "$PYTHON" -c 'import ensurepip' >/dev/null 2>&1; then
    PY_VER="$("$PYTHON" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
    echo "Installing python${PY_VER}-venv with apt..."
    sudo apt-get update
    sudo apt-get install -y "python${PY_VER}-venv"
fi

echo "Using $("$PYTHON" --version) at $(command -v "$PYTHON")"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    "$PYTHON" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -e "$REPO_DIR[dev]"

# .env holds the API key: readable by you only.
if [[ ! -f "$REPO_DIR/.env" ]]; then
    cp "$REPO_DIR/.env.example" "$REPO_DIR/.env"
    echo "Created .env from .env.example (add ANTHROPIC_API_KEY there for the AI score)."
fi
chmod 600 "$REPO_DIR/.env"

# Database and CVs: private to you.
mkdir -p "$REPO_DIR/data"
chmod 700 "$REPO_DIR/data"

if [[ $WANT_SERVICE -eq 1 ]]; then
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    sed "s|@REPO_DIR@|$REPO_DIR|g" "$REPO_DIR/deploy/job-tracker.service" > "$UNIT_DIR/job-tracker.service"
    systemctl --user daemon-reload
    systemctl --user enable --now job-tracker.service
    echo "Service installed. Status: systemctl --user status job-tracker"
    echo "To keep it running after you log out: sudo loginctl enable-linger $USER"
else
    echo
    echo "Done. Start the app with:"
    echo "  source .venv/bin/activate && python -m app"
fi
echo "Then open http://127.0.0.1:8000"
