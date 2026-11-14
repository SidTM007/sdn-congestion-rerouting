#!/usr/bin/env bash
# Runs the full experiment matrix (light/medium/heavy x static/adaptive, plus the hard and rapid scenarios)
# and draws the graphs.  Needs root; set RYU_MANAGER to the ryu-manager of the Python 3.8/3.9 venv.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RYU_MANAGER:=ryu-manager}"
for load in light medium heavy; do
  for mode in static adaptive; do
    sudo -E env RYU_MANAGER="$RYU_MANAGER" python3 experiments/run_experiment.py --scenario "$load" --mode "$mode" --out "results/${load}_${mode}"
    sudo mn -c >/dev/null 2>&1 || true
  done
done
for sc in hard rapid; do
  sudo -E env RYU_MANAGER="$RYU_MANAGER" python3 experiments/run_experiment.py --scenario "$sc" --mode adaptive --out "results/${sc}_adaptive"
  sudo mn -c >/dev/null 2>&1 || true
done
for load in light medium heavy; do
  python3 analysis/plot_results.py "results/${load}_adaptive" --compare "results/${load}_static"
done
python3 analysis/plot_results.py results/hard_adaptive
python3 analysis/plot_results.py results/rapid_adaptive
echo "All experiments finished. See results/*/metrics.json and the PNG graphs."
