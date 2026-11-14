#!/usr/bin/env python3
"""Background-traffic generator: creates congestion on demand (FR: background congestion generator).

Background flows use reserved UDP ports so the controller pins them and never reroutes them:
    --path A -> port 6001 (range 6001-6049, pinned to Path A)
    --path B -> port 6050 (range 6050-6099, pinned to Path B)
Start and stop times and the configuration of every run are appended to a run log.

Example (heavy load on Path A for 40 s):
    python3 traffic/background.py --path A --rate-mbps 8.5 --duration 40
(An iperf3 UDP run works too:  iperf3 -c 10.0.0.6 -u -b 8.5M -p 6001 -t 40  after  iperf3 -s -p 6001 on hb2.)
"""
import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common import topo_spec as T  # noqa: E402
from common import udp_proto as P  # noqa: E402
from traffic.udp_sender import run_sender  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--path", choices=["A", "B"], default="A")
    ap.add_argument("--port", type=int, default=None, help="override UDP port (must be inside the range of the chosen path)")
    ap.add_argument("--dst-ip", default=T.HOSTS["hb2"]["ip"])
    ap.add_argument("--rate-mbps", type=float, required=True)
    ap.add_argument("--packet-size", type=int, default=1000)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--flow-id", type=int, default=900)
    ap.add_argument("--runlog", default="logs/background_runs.csv")
    a = ap.parse_args(argv)
    try:
        port = P.validate_port(a.port if a.port is not None else T.BG_DEFAULT_PORT[a.path])
        lo, hi = T.BG_A_PORTS if a.path == "A" else T.BG_B_PORTS
        if not lo <= port <= hi:
            raise P.ValidationError("port %d is outside the background range %d-%d of path %s" % (port, lo, hi, a.path))
        t_start = time.time()
        stats = run_sender(a.dst_ip, port, a.rate_mbps, a.packet_size, a.duration, a.flow_id, quiet=True)
        t_stop = time.time()
    except P.ValidationError as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        t_stop = time.time()
        stats = {}
    os.makedirs(os.path.dirname(os.path.abspath(a.runlog)), exist_ok=True)
    new = not os.path.exists(a.runlog)
    with open(a.runlog, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["start", "stop", "path", "dst", "port", "rate_mbps", "packet_size", "achieved_mbps"])
        w.writerow(["%.3f" % t_start, "%.3f" % t_stop, a.path, a.dst_ip, port, a.rate_mbps, a.packet_size,
                    stats.get("achieved_mbps", "")])
    print("background run finished:", stats, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
