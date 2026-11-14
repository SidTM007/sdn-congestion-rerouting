#!/usr/bin/env bash
# Start the Ryu controller (inside the Python 3.8/3.9 venv). Usage: scripts/run_controller.sh [adaptive|static]
set -euo pipefail
cd "$(dirname "$0")/.."
export SDN_MODE="${1:-adaptive}"
export SDN_LOG_DIR="${SDN_LOG_DIR:-logs}"
export PYTHONPATH="$PWD"
exec ryu-manager --ofp-tcp-listen-port 6653 controller/sdn_controller.py
