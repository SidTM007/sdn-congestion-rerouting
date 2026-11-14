"""Compute time to detect, time to reroute, recovery time, throughput and loss from a run directory.

A run directory (written by experiments/run_experiment.py) contains:
    events.csv, utilisation.csv, flows.csv      written by the controller
    scenario_events.csv                         bg_start / bg_stop markers written by the runner
    rx_h3.csv, rx_h4.csv                        written by the receivers

Definitions (SRS Appendix A):
  time to detect    congestion start (bg_start)  -> congestion_declared event
  time to reroute   congestion_declared event    -> first sample where Path B shows the moved traffic
                    (1-second resolution); the controller's own rule-install delay is reported too
  recovery time     congestion start -> application throughput back to >= 90% of its pre-congestion mean
"""
import csv
import json
import os
import sys
from collections import defaultdict


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def app_throughput_series(run_dir, app_ports=(5001, 5099)):
    """{second: aggregate application throughput in Mbps} and per-flow loss deltas."""
    thr = defaultdict(float)
    lost_prev, lost_delta = {}, defaultdict(float)
    pkts = defaultdict(float)
    for name in ("rx_h3.csv", "rx_h4.csv"):
        for r in read_csv(os.path.join(run_dir, name)):
            port = int(r["port"])
            if not app_ports[0] <= port <= app_ports[1]:
                continue
            t = _f(r["time"])
            sec = int(round(t))
            thr[sec] += _f(r["throughput_mbps"], 0.0)
            pkts[sec] += _f(r["packets"], 0.0)
            key = (name, r["flow_id"], port)
            lt = _f(r["lost_total"], 0.0)
            lost_delta[sec] += max(0.0, lt - lost_prev.get(key, 0.0))
            lost_prev[key] = lt
    return dict(thr), dict(lost_delta), dict(pkts)


def compute_metrics(run_dir):
    ev = read_csv(os.path.join(run_dir, "events.csv"))
    util = read_csv(os.path.join(run_dir, "utilisation.csv"))
    scen = read_csv(os.path.join(run_dir, "scenario_events.csv"))
    thr, lost, pkts = app_throughput_series(run_dir)
    m = {"run_dir": run_dir}
    bg_starts = sorted(_f(r["time"]) for r in scen if r["event"] == "bg_start")
    bg_stops = sorted(_f(r["time"]) for r in scen if r["event"] == "bg_stop")
    t_start = bg_starts[0] if bg_starts else None
    t_stop = bg_stops[-1] if bg_stops else None
    declared = sorted(_f(r["time"]) for r in ev if r["event"] == "congestion_declared" and r["path"] == "A")
    reroutes = sorted(_f(r["time"]) for r in ev if r["event"] == "reroute")
    rebalances = sorted(_f(r["time"]) for r in ev if r["event"] == "rebalance")
    d = next((t for t in declared if t_start and t >= t_start), None)
    m["time_to_detect_s"] = round(d - t_start, 2) if d and t_start else None
    rr = next((t for t in reroutes if d and t >= d), None)
    m["time_to_reroute_install_s"] = round(rr - d, 3) if rr and d else None
    if d:
        base = [_f(r["util_B"]) for r in util if _f(r["time"]) < d and _f(r["util_B"]) is not None]
        base = max(base) if base else 0.0
        tb = next((_f(r["time"]) for r in util if _f(r["time"]) >= d and _f(r["util_B"]) is not None
                   and _f(r["util_B"]) > base + 0.05), None)
        m["time_to_reroute_traffic_s"] = round(tb - d, 2) if tb else None
    else:
        m["time_to_reroute_traffic_s"] = None
    m["reroutes"] = len(reroutes)
    m["rebalances"] = len(rebalances)
    m["blocked_decisions"] = sum(1 for r in ev if r["event"] == "reroute_blocked")
    # flapping check: moves per flow must be separated by at least the hold-down time
    moves = defaultdict(list)
    for r in ev:
        if r["event"] in ("reroute", "rebalance"):
            moves[r["details"].split(";")[0]].append(_f(r["time"]))
    m["min_gap_between_moves_of_same_flow_s"] = min((b - a for ts in moves.values() for a, b in zip(ts, ts[1:])), default=None)
    # throughput / loss before, during, after
    secs = sorted(thr)
    def window(lo, hi):
        s = [x for x in secs if (lo is None or x >= lo) and (hi is None or x < hi)]
        if not s:
            return None, None
        avg = sum(thr[x] for x in s) / len(s)
        sent = sum(pkts.get(x, 0) for x in s) + sum(lost.get(x, 0) for x in s)
        loss = 100.0 * sum(lost.get(x, 0) for x in s) / sent if sent else 0.0
        return round(avg, 3), round(loss, 3)
    if t_start and t_stop:
        m["throughput_before_mbps"], m["loss_before_pct"] = window(None, t_start)
        m["throughput_during_mbps"], m["loss_during_pct"] = window(t_start, t_stop)
        m["throughput_after_mbps"], m["loss_after_pct"] = window(t_stop + 5, None)
        pre = [thr[x] for x in secs if x < t_start - 1]
        if pre:
            ref = sum(pre) / len(pre)
            rec = None
            for i, x in enumerate(secs):
                if x >= t_start and all(thr.get(y, 0) >= 0.9 * ref for y in secs[i:i + 3]) and len(secs[i:i + 3]) == 3:
                    rec = x - t_start
                    break
            m["recovery_time_s"] = round(rec, 1) if rec is not None else None
            m["pre_congestion_throughput_mbps"] = round(ref, 3)
    return m


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        print("usage: python3 analysis/metrics.py RUN_DIR [RUN_DIR ...]")
        return 2
    for d in argv:
        m = compute_metrics(d)
        with open(os.path.join(d, "metrics.json"), "w") as fh:
            json.dump(m, fh, indent=2)
        print(json.dumps(m, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
