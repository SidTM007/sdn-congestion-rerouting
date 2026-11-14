#!/usr/bin/env bash
# Create the Python 3.8/3.9 virtual environment that Ryu 4.34 needs.
# Ubuntu 22.04 ships Python 3.10, so install 3.8/3.9 first, e.g.:
#   sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.9 python3.9-venv
# (or use pyenv).  Then run this script.
set -euo pipefail
PY=$(command -v python3.9 || command -v python3.8 || true)
if [ -z "$PY" ]; then echo "python3.8 or python3.9 not found - see comments in this script"; exit 1; fi
"$PY" -m venv ~/sdn-venv
~/sdn-venv/bin/pip install --upgrade pip
~/sdn-venv/bin/pip install -r "$(dirname "$0")/../requirements-controller.txt"
echo "Done. Controller: ~/sdn-venv/bin/ryu-manager   (export RYU_MANAGER=~/sdn-venv/bin/ryu-manager)"
