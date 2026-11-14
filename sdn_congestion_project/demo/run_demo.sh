#!/usr/bin/env bash
# Scripted live demonstration (FR: scripted demo, TC-18).
# Starts the controller and topology, runs UDP flows, turns congestion ON live (Path A, then Path B too),
# prints the flow tables before / after the reroute, then turns the load OFF so traffic rebalances.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RYU_MANAGER:=ryu-manager}"
sudo mn -c >/dev/null 2>&1 || true
sudo -E env RYU_MANAGER="$RYU_MANAGER" python3 experiments/run_experiment.py --scenario demo --mode adaptive --out results/demo --narrate
python3 analysis/plot_results.py results/demo || true
echo "Demo finished. Watch results/demo/events.csv for every decision, graphs in results/demo/*.png"
