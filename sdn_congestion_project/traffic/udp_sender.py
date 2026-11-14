#!/usr/bin/env python3
"""UDP sender with configurable rate, packet size, flow id and duration (FR: UDP sender).

Each datagram carries flow_id, a sequence number that increases by one per packet and the
send timestamp (see common/udp_proto.py).  The configured rate counts UDP payload bytes.

Example:
    python3 traffic/udp_sender.py --dst-ip 10.0.0.3 --dst-port 5001 --rate-mbps 2 --duration 30 --flow-id 1
"""
import argparse
import csv
import os
import socket
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common import udp_proto as P  # noqa: E402


def run_sender(dst_ip, dst_port, rate_mbps, size, duration, flow_id=1, bind_ip=None, src_port=None,
               log_path=None, quiet=False):
    dst_ip, dst_port = P.validate_ip(dst_ip), P.validate_port(dst_port)
    rate_mbps, size, duration = P.validate_rate(rate_mbps), P.validate_size(size), P.validate_duration(duration)
    if not 0 <= int(flow_id) <= 65535:
        raise P.ValidationError("flow id must be 0-65535")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if bind_ip or src_port:
        sock.bind((bind_ip or "", int(src_port or 0)))
    interval = size * 8.0 / (rate_mbps * 1e6)       # seconds between packets
    start = time.time()
    t0 = time.perf_counter()
    next_t, seq, last_log, sent_bytes_last = t0, 0, t0, 0
    rows = []
    end = t0 + duration
    while True:
        now = time.perf_counter()
        if now >= end:
            break
        if now < next_t:
            gap = next_t - now
            time.sleep(gap - 0.0005 if gap > 0.001 else 0)
            continue
        sock.sendto(P.pack_packet(flow_id, seq, time.time(), size), (dst_ip, dst_port))
        seq += 1
        next_t += interval
        if now - next_t > 0.05:         # fell behind (busy host): do not burst to catch up
            next_t = now
        if log_path and now - last_log >= 1.0:
            rows.append((round(time.time(), 3), seq, (seq * size - sent_bytes_last) * 8 / (now - last_log) / 1e6))
            last_log, sent_bytes_last = now, seq * size
    elapsed = time.perf_counter() - t0
    stats = dict(flow_id=flow_id, dst=f"{dst_ip}:{dst_port}", packets=seq, bytes=seq * size, start=start,
                 duration_s=round(elapsed, 3), achieved_mbps=round(seq * size * 8 / elapsed / 1e6, 4),
                 configured_mbps=rate_mbps)
    if log_path:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(log_path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time", "packets_sent", "rate_mbps"])
            w.writerows(rows)
    if not quiet:
        print("sender done:", stats, flush=True)
    sock.close()
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dst-ip", required=True)
    ap.add_argument("--dst-port", required=True, type=int)
    ap.add_argument("--rate-mbps", type=float, required=True, help="UDP payload rate in Mbps")
    ap.add_argument("--packet-size", type=int, default=1000, help="UDP payload bytes (14-1472)")
    ap.add_argument("--duration", type=float, required=True, help="seconds")
    ap.add_argument("--flow-id", type=int, default=1)
    ap.add_argument("--bind-ip", default=None)
    ap.add_argument("--src-port", type=int, default=None)
    ap.add_argument("--log", default=None, help="optional CSV with per-second sent rate")
    a = ap.parse_args(argv)
    try:
        run_sender(a.dst_ip, a.dst_port, a.rate_mbps, a.packet_size, a.duration, a.flow_id, a.bind_ip, a.src_port, a.log)
    except P.ValidationError as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
