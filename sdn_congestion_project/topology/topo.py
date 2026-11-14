#!/usr/bin/env python3
"""Mininet topology: two parallel bandwidth-limited paths (SRS Section 2.1, FR-001..FR-004).

    h1, h2, hb1 --- s1 ==== s2 ==== s4 --- h3, h4, hb2      Path A (main)
                     \\                 /
                      ===== s3 ========                      Path B (alternate)

Inter-switch links: TCLink with bandwidth, delay and queue size (defaults 10 Mbps, 5 ms, 100 packets).
Host links: 100 Mbps so that the inter-switch links are the bottleneck.
Run as root:  sudo python3 topology/topo.py            (starts a CLI; start the Ryu controller first)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common import topo_spec as T  # noqa: E402


def build_network(capacity_mbps=10.0, delay_ms=5, queue_size=100, host_capacity_mbps=100.0,
                  controller_ip="127.0.0.1", controller_port=6653):
    from mininet.net import Mininet
    from mininet.node import RemoteController, OVSSwitch
    from mininet.link import TCLink

    for name, val in (("capacity", capacity_mbps), ("host capacity", host_capacity_mbps)):
        if val <= 0:
            raise ValueError("%s must be greater than 0" % name)
    if queue_size < 1 or delay_ms < 0:
        raise ValueError("queue size must be >= 1 and delay >= 0")

    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=False, build=False)
    net.addController("c0", controller=RemoteController, ip=controller_ip, port=controller_port)
    switches = {}
    for dpid in T.SWITCHES:
        switches[dpid] = net.addSwitch("s%d" % dpid, dpid="%016x" % dpid, protocols="OpenFlow13", failMode="secure")
    for name, h in T.HOSTS.items():
        host = net.addHost(name, ip="%s/24" % h["ip"], mac=h["mac"])
        net.addLink(host, switches[h["dpid"]], port2=h["port"], bw=host_capacity_mbps)
    for dpid_a, port_a, dpid_b, port_b in T.INTER_SWITCH_LINKS:
        net.addLink(switches[dpid_a], switches[dpid_b], port1=port_a, port2=port_b,
                    bw=capacity_mbps, delay="%sms" % delay_ms, max_queue_size=queue_size)
    net.build()
    return net


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capacity-mbps", type=float, default=10.0)
    ap.add_argument("--delay-ms", type=float, default=5)
    ap.add_argument("--queue-size", type=int, default=100)
    ap.add_argument("--host-capacity-mbps", type=float, default=100.0)
    ap.add_argument("--controller-ip", default="127.0.0.1")
    ap.add_argument("--controller-port", type=int, default=6653)
    ap.add_argument("--no-cli", action="store_true", help="start, run pingall and exit")
    a = ap.parse_args(argv)
    from mininet.log import setLogLevel
    from mininet.cli import CLI
    setLogLevel("info")
    try:
        net = build_network(a.capacity_mbps, a.delay_ms, a.queue_size, a.host_capacity_mbps, a.controller_ip, a.controller_port)
    except ValueError as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    net.start()
    try:
        loss = net.pingAll()
        print("pingall packet loss: %s%%" % loss)
        if not a.no_cli:
            CLI(net)
    finally:
        net.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
