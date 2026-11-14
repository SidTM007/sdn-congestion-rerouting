"""Graphs for one run or a static-vs-adaptive comparison (matplotlib).

    python3 analysis/plot_results.py results/heavy_adaptive
    python3 analysis/plot_results.py results/heavy_adaptive --compare results/heavy_static
Outputs PNG files into the run directory (utilisation.png, throughput.png, comparison.png).
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from analysis.metrics import read_csv, app_throughput_series, compute_metrics, _f  # noqa: E402


def _t0(run_dir):
    util = read_csv(os.path.join(run_dir, "utilisation.csv"))
    return _f(util[0]["time"]) if util else 0.0


def plot_run(run_dir):
    t0 = _t0(run_dir)
    util = read_csv(os.path.join(run_dir, "utilisation.csv"))
    ev = read_csv(os.path.join(run_dir, "events.csv"))
    scen = read_csv(os.path.join(run_dir, "scenario_events.csv"))
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for p, c in (("A", "#B3472A"), ("B", "#1D5FA3")):
        pts = [(_f(r["time"]) - t0, _f(r["util_" + p])) for r in util if _f(r["util_" + p]) is not None]
        ax.plot([x for x, _ in pts], [100 * y for _, y in pts], label="Path %s utilisation" % p, color=c)
    ax.axhline(80, ls="--", color="grey", lw=1, label="congested threshold 80%")
    ax.axhline(50, ls=":", color="grey", lw=1, label="clear threshold 50%")
    for r in ev:
        if r["event"] in ("congestion_declared", "reroute", "rebalance"):
            ax.axvline(_f(r["time"]) - t0, color="black", lw=0.8, alpha=0.5)
            ax.text(_f(r["time"]) - t0, 103, r["event"].replace("congestion_declared", "detect"), rotation=90, fontsize=7, va="bottom")
    for r in scen:
        if r["event"] in ("bg_start", "bg_stop"):
            ax.axvline(_f(r["time"]) - t0, color="orange", lw=1, ls="-.")
    ax.set_xlabel("time (s)"); ax.set_ylabel("utilisation (%)"); ax.set_ylim(0, 125); ax.legend(loc="lower right", fontsize=8)
    ax.set_title("Path utilisation and controller decisions (orange = background start/stop)")
    fig.tight_layout(); fig.savefig(os.path.join(run_dir, "utilisation.png"), dpi=150); plt.close(fig)

    thr, lost, _p = app_throughput_series(run_dir)
    fig, ax = plt.subplots(figsize=(10, 4))
    xs = sorted(thr)
    ax.plot([x - t0 for x in xs], [thr[x] for x in xs], color="#0B7A6E", label="application throughput (received)")
    for r in scen:
        if r["event"] in ("bg_start", "bg_stop"):
            ax.axvline(_f(r["time"]) - t0, color="orange", lw=1, ls="-.")
    ax.set_xlabel("time (s)"); ax.set_ylabel("Mbps"); ax.legend(); ax.set_title("Application throughput at the receivers")
    fig.tight_layout(); fig.savefig(os.path.join(run_dir, "throughput.png"), dpi=150); plt.close(fig)


def plot_compare(adaptive_dir, static_dir, out_dir):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4))
    for d, label, c in ((adaptive_dir, "adaptive", "#0B7A6E"), (static_dir, "static", "#B3472A")):
        thr, _l, _p = app_throughput_series(d)
        t0 = _t0(d)
        xs = sorted(thr)
        axes[0].plot([x - t0 for x in xs], [thr[x] for x in xs], label=label, color=c)
    axes[0].set_xlabel("time (s)"); axes[0].set_ylabel("application throughput (Mbps)"); axes[0].legend(); axes[0].set_title("Throughput")
    ma, ms = compute_metrics(adaptive_dir), compute_metrics(static_dir)
    names, va, vs = ["before", "during", "after"], [], []
    for k in names:
        va.append(ma.get("loss_%s_pct" % k) or 0.0); vs.append(ms.get("loss_%s_pct" % k) or 0.0)
    w = 0.35
    axes[1].bar([i - w / 2 for i in range(3)], va, w, label="adaptive", color="#0B7A6E")
    axes[1].bar([i + w / 2 for i in range(3)], vs, w, label="static", color="#B3472A")
    axes[1].set_xticks(range(3)); axes[1].set_xticklabels(names); axes[1].set_ylabel("packet loss (%)"); axes[1].legend(); axes[1].set_title("Loss")
    fig.tight_layout(); fig.savefig(os.path.join(out_dir, "comparison.png"), dpi=150); plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir")
    ap.add_argument("--compare", help="static run directory to compare against (run_dir = adaptive run)")
    a = ap.parse_args(argv)
    plot_run(a.run_dir)
    if a.compare:
        plot_run(a.compare)
        plot_compare(a.run_dir, a.compare, a.run_dir)
    print("graphs written to", a.run_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
