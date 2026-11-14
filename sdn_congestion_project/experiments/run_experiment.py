#!/usr/bin/env python3
"""Scenario runner: starts the Ryu controller, the Mininet topology, receivers and senders, applies the
scenario timeline, then writes metrics (SRS Sections 4.8, 6.3, 6.4; test cases TC-07 ... TC-18).

Run as root, with Mininet available to this Python (system python3) and Ryu installed in the venv:
    sudo -E python3 experiments/run_experiment.py --scenario heavy --mode adaptive \\
         --ryu-manager ~/venv38/bin/ryu-manager --out results/heavy_adaptive
Scenarios: basic, concurrent, spike, light, medium, heavy, hard, rapid, demo  (see SCENARIOS below).
"""
import argparse
import csv
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
from common import topo_spec as T  # noqa: E402

# application flows: (src host, dst host, udp port, rate Mbps, flow id)
STD_FLOWS = [("h1", "h3", 5001, 1.5, 1), ("h2", "h4", 5003, 1.5, 2)]


def _alternating(start, stop, step, rate):
    out, t, path = [], start, "A"
    while t < stop:
        out.append((path, rate, t, min(t + step, stop)))
        t, path = t + step, ("B" if path == "A" else "A")
    return out


# bg: (path, rate Mbps, start s, stop s) -- load levels from SRS Section 6.3 (offered load on Path A incl. 3 Mbps of app flows)
SCENARIOS = {
    "basic":      dict(tc="TC-07", flows=[("h1", "h3", 5001, 4.0, 1)], bg=[], duration=25),
    "concurrent": dict(tc="TC-08", flows=[("h1", "h3", 5001, 2.0, 1), ("h2", "h4", 5003, 2.0, 2), ("h1", "h3", 5002, 1.0, 3)], bg=[], duration=25),
    "spike":      dict(tc="TC-10", flows=STD_FLOWS, bg=[("A", 8.5, 20, 22)], duration=45),    # 2 s spike < detection window
    "light":      dict(tc="TC-16", flows=STD_FLOWS, bg=[("A", 0.5, 20, 60)], duration=80),    # ~35% offered
    "medium":     dict(tc="TC-16", flows=STD_FLOWS, bg=[("A", 5.75, 20, 60)], duration=80),   # ~87%
    "heavy":      dict(tc="TC-09/12/13/16/17", flows=STD_FLOWS, bg=[("A", 8.5, 20, 60)], duration=90),   # ~115%
    "hard":       dict(tc="TC-14", flows=STD_FLOWS, bg=[("A", 8.5, 20, 90), ("B", 8.5, 40, 90)], duration=110),
    "rapid":      dict(tc="TC-15", flows=STD_FLOWS, bg=_alternating(20, 80, 6, 8.5), duration=95),
    "demo":       dict(tc="TC-18", flows=STD_FLOWS, bg=[("A", 8.5, 15, 55), ("B", 8.5, 35, 55)], duration=90),
}


def dump_flows(label):
    """Show the per-flow (priority 100) and reverse (priority 90) rules on s1, s2, s3, s4 (FR: dump-flows)."""
    print("\n===== flow tables: %s =====" % label, flush=True)
    for s in ("s1", "s2", "s3", "s4"):
        out = subprocess.run(["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", s], capture_output=True, text=True).stdout
        lines = [l.strip() for l in out.splitlines() if "priority=100" in l or "priority=90" in l]
        print("--- %s ---" % s)
        print("\n".join(lines) if lines else "(no per-flow rules)", flush=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    ap.add_argument("--mode", choices=["adaptive", "static"], default="adaptive")
    ap.add_argument("--out", default=None, help="output directory (default results/<scenario>_<mode>)")
    ap.add_argument("--ryu-manager", default=os.environ.get("RYU_MANAGER", "ryu-manager"))
    ap.add_argument("--config", default=None, help="JSON controller configuration (thresholds, timers)")
    ap.add_argument("--controller-port", type=int, default=6653)
    ap.add_argument("--capacity-mbps", type=float, default=10.0)
    ap.add_argument("--delay-ms", type=float, default=5)
    ap.add_argument("--queue-size", type=int, default=100)
    ap.add_argument("--narrate", action="store_true", help="print explanations and flow tables (used by the demo)")
    a = ap.parse_args(argv)
    if os.geteuid() != 0:
        print("error: Mininet needs root; run with sudo -E", file=sys.stderr)
        return 2
    sc = SCENARIOS[a.scenario]
    narrate = a.narrate or a.scenario == "demo"
    out = os.path.abspath(a.out or os.path.join(ROOT, "results", "%s_%s" % (a.scenario, a.mode)))
    os.makedirs(out, exist_ok=True)
    say = (lambda m: print("\n>>> " + m, flush=True)) if narrate else (lambda m: None)

    env = os.environ.copy()
    env.update(SDN_MODE=a.mode, SDN_LOG_DIR=out, PYTHONPATH=ROOT)
    if a.config:
        env["SDN_CONFIG"] = os.path.abspath(a.config)
    ctrl = subprocess.Popen([a.ryu_manager, "--ofp-tcp-listen-port", str(a.controller_port),
                             os.path.join(ROOT, "controller", "sdn_controller.py")],
                            env=env, stdout=open(os.path.join(out, "controller.out"), "w"), stderr=subprocess.STDOUT)
    time.sleep(4)
    from topology.topo import build_network
    net = build_network(a.capacity_mbps, a.delay_ms, a.queue_size, controller_port=a.controller_port)
    procs, scen_rows = [], []

    def mark(event, details=""):
        scen_rows.append(("%.3f" % time.time(), event, details))

    try:
        net.start()
        time.sleep(4)
        say("Checking connectivity (ARP is answered by the controller, no broadcast storm) ...")
        loss = net.pingAll()
        print("pingall loss: %s%%" % loss, flush=True)
        # receivers
        for host, ports in (("h3", [5001, 5002]), ("h4", [5003, 5004]), ("hb2", [6001, 6050])):
            procs.append(net.get(host).popen([sys.executable, os.path.join(ROOT, "traffic", "udp_receiver.py"), "--ports"] + [str(p) for p in ports]
                                             + ["--interval", "1", "--log", os.path.join(out, "rx_%s.csv" % host)],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(1)
        t0 = time.time()
        duration = sc["duration"]
        mark("flows_start", a.scenario)
        say("Starting %d application flows (total %.1f Mbps) ..." % (len(sc["flows"]), sum(f[3] for f in sc["flows"])))
        for src, dst, port, rate, fid in sc["flows"]:
            procs.append(net.get(src).popen([sys.executable, os.path.join(ROOT, "traffic", "udp_sender.py"), "--dst-ip", T.HOSTS[dst]["ip"],
                                             "--dst-port", str(port), "--rate-mbps", str(rate), "--duration", str(duration), "--flow-id", str(fid)],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        timeline = []
        for path, rate, start, stop in sc["bg"]:
            timeline.append((start, "bg_start", (path, rate, stop - start)))
            timeline.append((stop, "bg_stop", (path, rate, 0)))
        timeline.sort(key=lambda x: x[0])
        if narrate:
            time.sleep(8)
            dump_flows("BEFORE congestion (all flows on Path A)")
        for t, kind, (path, rate, dur) in timeline:
            time.sleep(max(0.0, t0 + t - time.time()))
            if kind == "bg_start":
                say("Turning ON background congestion on Path %s at %.2f Mbps ..." % (path, rate))
                mark("bg_start", "path=%s rate=%s" % (path, rate))
                procs.append(net.get("hb1").popen([sys.executable, os.path.join(ROOT, "traffic", "background.py"), "--path", path,
                                                   "--rate-mbps", str(rate), "--duration", str(dur),
                                                   "--runlog", os.path.join(out, "background_runs.csv")],
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
                if narrate and path == "A":
                    time.sleep(12)
                    dump_flows("AFTER detection and reroute (flows moved to Path B)")
            else:
                say("Background congestion on Path %s stopped." % path)
                mark("bg_stop", "path=%s" % path)
        time.sleep(max(0.0, t0 + duration + 2 - time.time()))
        if narrate:
            dump_flows("END of run (after rebalancing)")
        mark("end", "")
    finally:
        for p in procs:
            try:
                p.send_signal(signal.SIGINT)
            except Exception:
                pass
        time.sleep(1.5)
        for p in procs:
            try:
                p.kill()
            except Exception:
                pass
        net.stop()
        ctrl.terminate()
        with open(os.path.join(out, "scenario_events.csv"), "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "event", "details"])
            w.writerows(scen_rows)
    from analysis.metrics import compute_metrics
    import json
    m = compute_metrics(out)
    m.update(scenario=a.scenario, mode=a.mode, test_case=sc["tc"])
    with open(os.path.join(out, "metrics.json"), "w") as fh:
        json.dump(m, fh, indent=2)
    print("\n===== metrics (%s, %s) =====" % (a.scenario, a.mode))
    print(json.dumps(m, indent=2))
    print("results in", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
