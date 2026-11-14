"""Single source of truth for the topology (used by the Mininet script AND the controller).

Topology (SRS Section 2.1):

    h1, h2, hb1 --- s1 ===(Path A)=== s2 === s4 --- h3, h4, hb2
                      \\                       /
                       ===(Path B)=== s3 ====

Switch port numbers are fixed explicitly in topology/topo.py so the controller
can rely on them.
"""

HOSTS = {
    # name: ip, mac, dpid of the switch it is attached to, port on that switch
    "h1":  dict(ip="10.0.0.1", mac="00:00:00:00:00:01", dpid=1, port=1),
    "h2":  dict(ip="10.0.0.2", mac="00:00:00:00:00:02", dpid=1, port=2),
    "hb1": dict(ip="10.0.0.5", mac="00:00:00:00:00:05", dpid=1, port=3),
    "h3":  dict(ip="10.0.0.3", mac="00:00:00:00:00:03", dpid=4, port=1),
    "h4":  dict(ip="10.0.0.4", mac="00:00:00:00:00:04", dpid=4, port=2),
    "hb2": dict(ip="10.0.0.6", mac="00:00:00:00:00:06", dpid=4, port=3),
}
HOST_BY_IP = {h["ip"]: name for name, h in HOSTS.items()}
SWITCHES = [1, 2, 3, 4]

# (dpid_a, port_a, dpid_b, port_b): inter-switch links (each limited to 10 Mbps by default)
INTER_SWITCH_LINKS = [
    (1, 4, 2, 1),  # Path A: s1 - s2
    (2, 2, 4, 4),  # Path A: s2 - s4
    (1, 5, 3, 1),  # Path B: s1 - s3
    (3, 2, 4, 5),  # Path B: s3 - s4
]

PATHS = ("A", "B")
OTHER = {"A": "B", "B": "A"}
TRANSIT = {"A": 2, "B": 3}  # transit switch of each path

# Forward-direction (sender side -> receiver side) egress ports used for path utilisation (FR-023)
FORWARD_PORTS = {
    "A": [(1, 4), (2, 2)],
    "B": [(1, 5), (3, 2)],
}

# UDP destination port ranges (SRS Section 3.4)
APP_PORTS = (5001, 5099)      # reroutable application flows
BG_A_PORTS = (6001, 6049)     # background, pinned to Path A
BG_B_PORTS = (6050, 6099)     # background, pinned to Path B
BG_DEFAULT_PORT = {"A": 6001, "B": 6050}


def classify_port(udp_dst):
    """Return ('app'|'bg', pinned_path or None) for a UDP destination port."""
    if BG_A_PORTS[0] <= udp_dst <= BG_A_PORTS[1]:
        return "bg", "A"
    if BG_B_PORTS[0] <= udp_dst <= BG_B_PORTS[1]:
        return "bg", "B"
    return "app", None


def uplink_port(path):
    """Port on s1 (towards s4) and on s4 (towards s1) for the given path."""
    return 4 if path == "A" else 5


def out_port_toward(dpid, dst_host, path):
    """Port on switch `dpid` that leads towards host `dst_host` when using `path`."""
    dst_dpid = HOSTS[dst_host]["dpid"]
    if dpid == dst_dpid:
        return HOSTS[dst_host]["port"]
    if dpid in (1, 4):
        return uplink_port(path)
    if dpid in (2, 3):
        return 2 if dst_dpid == 4 else 1
    raise ValueError("unknown switch %r" % dpid)
