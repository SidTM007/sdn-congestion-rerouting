#!/usr/bin/env bash
# System packages for the emulation (Ubuntu 22.04).
set -euo pipefail
sudo apt update
sudo apt install -y mininet openvswitch-switch iperf3 python3-pip tcpdump
python3 -m pip install -r "$(dirname "$0")/../requirements-analysis.txt"
