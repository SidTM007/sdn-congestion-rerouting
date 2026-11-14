#!/usr/bin/env python3
"""UDP receiver: per-flow throughput, packet loss and one-way delay, logged to CSV (FR: receiver metrics).

Loss is estimated from gaps in the sequence numbers; delay uses the sender timestamp (all hosts share
the clock of the emulation machine).  One CSV row per flow per interval:
    time, flow_id, port, packets, bytes, throughput_mbps, lost_total, loss_pct, delay_ms_avg, delay_ms_max

Example:
    python3 traffic/udp_receiver.py --ports 5001 5002 --interval 1 --log results/rx_h3.csv
"""
import argparse
import csv
import json
import os
import selectors
import socket
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common import udp_proto as P  # noqa: E402


class FlowStats:
    def __init__(self):
        self.received = 0
        self.max_seq = -1
        self.bytes_total = 0
        self.i_packets = self.i_bytes = 0
        self.i_delay_sum = self.i_delay_max = 0.0

    def add(self, seq, size, delay):
        self.received += 1
        self.max_seq = max(self.max_seq, seq)
        self.bytes_total += size
        self.i_packets += 1
        self.i_bytes += size
        self.i_delay_sum += delay
        self.i_delay_max = max(self.i_delay_max, delay)

    @property
    def lost_total(self):
        return max(0, (self.max_seq + 1) - self.received)

    def reset_interval(self):
        self.i_packets = self.i_bytes = 0
        self.i_delay_sum = self.i_delay_max = 0.0


def run_receiver(ports, interval=1.0, duration=None, log_path=None, bind_ip="0.0.0.0", quiet=False, stop_flag=None):
    ports = [P.validate_port(p) for p in ports]
    if not ports:
        raise P.ValidationError("at least one port is required")
    sel = selectors.DefaultSelector()
    socks = []
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        s.bind((bind_ip, port))
        s.setblocking(False)
        sel.register(s, selectors.EVENT_READ, port)
        socks.append(s)
    fh = None
    writer = None
    if log_path:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        fh = open(log_path, "w", newline="")
        writer = csv.writer(fh)
        writer.writerow(["time", "flow_id", "port", "packets", "bytes", "throughput_mbps", "lost_total",
                         "loss_pct", "delay_ms_avg", "delay_ms_max"])
        fh.flush()
    flows = {}
    t_start = time.time()
    t_last = t_start
    summary = {}

    def flush(now, dt):
        for (fid, port), st in sorted(flows.items()):
            expected = st.max_seq + 1
            loss_pct = 100.0 * st.lost_total / expected if expected else 0.0
            avg = (st.i_delay_sum / st.i_packets * 1000) if st.i_packets else 0.0
            thr = st.i_bytes * 8 / dt / 1e6
            if writer:
                writer.writerow(["%.3f" % now, fid, port, st.i_packets, st.i_bytes, "%.4f" % thr, st.lost_total,
                                 "%.3f" % loss_pct, "%.3f" % avg, "%.3f" % (st.i_delay_max * 1000)])
            st.reset_interval()
        if fh:
            fh.flush()

    try:
        while True:
            now = time.time()
            if duration is not None and now - t_start >= duration:
                break
            if stop_flag is not None and stop_flag.is_set():
                break
            for key, _ in sel.select(timeout=0.05):
                sock = key.fileobj
                while True:
                    try:
                        data, _addr = sock.recvfrom(2048)
                    except BlockingIOError:
                        break
                    hdr = P.unpack_header(data)
                    if hdr is None:
                        continue
                    fid, seq, ts = hdr
                    st = flows.setdefault((fid, key.data), FlowStats())
                    st.add(seq, len(data), max(0.0, time.time() - ts))
            now = time.time()
            if now - t_last >= interval:
                flush(now, now - t_last)
                t_last = now
    except KeyboardInterrupt:
        pass
    finally:
        now = time.time()
        if now - t_last > 0.05:
            flush(now, now - t_last)
        for (fid, port), st in flows.items():
            exp = st.max_seq + 1
            summary["%d:%d" % (fid, port)] = dict(received=st.received, expected=exp, lost=st.lost_total,
                                                  loss_pct=round(100.0 * st.lost_total / exp, 3) if exp else 0.0,
                                                  bytes=st.bytes_total)
        for s in socks:
            s.close()
        if fh:
            fh.close()
        if not quiet:
            print("receiver summary:", json.dumps(summary), flush=True)
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ports", nargs="+", type=int, required=True)
    ap.add_argument("--interval", type=float, default=1.0, help="report interval in seconds")
    ap.add_argument("--duration", type=float, default=None, help="stop after this many seconds (default: run until Ctrl-C)")
    ap.add_argument("--log", default=None, help="CSV log file")
    ap.add_argument("--bind-ip", default="0.0.0.0")
    a = ap.parse_args(argv)
    try:
        if a.interval <= 0:
            raise P.ValidationError("interval must be greater than 0")
        run_receiver(a.ports, a.interval, a.duration, a.log, a.bind_ip)
    except P.ValidationError as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
