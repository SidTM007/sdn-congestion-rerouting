"""Smoke test of the Ryu controller code WITHOUT Ryu/OpenFlow: minimal stand-ins for the ryu modules and a
fake datapath record the messages the controller would send.  Checks base rules, make-before-break order,
reroute / rebalance rule updates and path utilisation handling.  (Real behaviour still has to be verified in Mininet.)"""
import sys
import types

import pytest


class FakeMsg:
    def __init__(self, kind, **kw):
        self.kind = kind
        self.__dict__.update(kw)


class FakeParser:
    def OFPMatch(self, **kw): return dict(kw)
    def OFPActionOutput(self, port, max_len=0): return ("out", port)
    def OFPInstructionActions(self, typ, actions): return actions
    def OFPFlowMod(self, **kw):
        kw["instructions"] = kw["instructions"][0]      # single apply-actions instruction -> its action list
        return FakeMsg("flowmod", **kw)
    def OFPPacketOut(self, **kw): return FakeMsg("packetout", **kw)
    def OFPPortStatsRequest(self, *a): return FakeMsg("portreq")
    def OFPFlowStatsRequest(self, *a): return FakeMsg("flowreq")


class FakeOfp:
    OFPP_CONTROLLER, OFPP_ANY, OFPCML_NO_BUFFER, OFPIT_APPLY_ACTIONS = 0xfffffffd, 0xffffffff, 0xffff, 4
    OFPFF_SEND_FLOW_REM, OFP_NO_BUFFER = 1, 0xffffffff


class FakeDP:
    log = []          # global, ordered record of every message sent: (dpid, message)

    def __init__(self, dpid):
        self.id, self.ofproto, self.ofproto_parser = dpid, FakeOfp(), FakeParser()

    def send_msg(self, m): FakeDP.log.append((self.id, m))


@pytest.fixture()
def controller(tmp_path, monkeypatch):
    def mod(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        sys.modules[name] = m
        return m
    class RyuApp:
        def __init__(self, *a, **k):
            import logging
            self.logger = logging.getLogger("stub")
    mod("ryu"); mod("ryu.base"); mod("ryu.base.app_manager", RyuApp=RyuApp)
    mod("ryu.controller")
    ev = types.SimpleNamespace(**{n: n for n in ("EventOFPSwitchFeatures", "EventOFPStateChange", "EventOFPPacketIn",
                                                  "EventOFPFlowRemoved", "EventOFPPortStatsReply", "EventOFPFlowStatsReply")})
    mod("ryu.controller.ofp_event", **ev.__dict__)
    mod("ryu.controller.handler", CONFIG_DISPATCHER=1, MAIN_DISPATCHER=2, DEAD_DISPATCHER=3, set_ev_cls=lambda *a, **k: (lambda f: f))
    mod("ryu.lib"); mod("ryu.lib.hub", spawn=lambda *a, **k: None, sleep=lambda s: None)
    pk = mod("ryu.lib.packet"); 
    for n in ("packet", "ethernet", "ipv4", "udp", "arp"):
        setattr(pk, n, mod("ryu.lib.packet." + n, ARP_REQUEST=1, ARP_REPLY=2))
    mod("ryu.ofproto"); mod("ryu.ofproto.ofproto_v1_3", OFP_VERSION=4)
    ryu = sys.modules["ryu"]; ryu.base = sys.modules["ryu.base"]; ryu.controller = sys.modules["ryu.controller"]; ryu.lib = sys.modules["ryu.lib"]; ryu.ofproto = sys.modules["ryu.ofproto"]
    monkeypatch.setenv("SDN_LOG_DIR", str(tmp_path))
    sys.modules.pop("controller.sdn_controller", None)
    from controller.sdn_controller import CongestionController
    c = CongestionController()
    FakeDP.log.clear()
    for d in (1, 2, 3, 4):
        dp = FakeDP(d)
        c.datapaths[d] = dp
        c.install_base_rules(dp)
    return c


def flowmods(c, dpid=None):
    return [(d, m) for d, m in FakeDP.log if m.kind == "flowmod" and (dpid is None or d == dpid)]


def test_base_rules_default_path_a(controller):
    c = controller
    s1 = {(m.priority, tuple(sorted(m.match.items()))): m for d, m in flowmods(c, 1)}
    # destination h3 (10.0.0.3) is reached via port 4 (Path A) at s1
    key = (10, tuple(sorted({"eth_type": 0x0800, "ipv4_dst": "10.0.0.3"}.items())))
    assert s1[key].instructions == [("out", 4)]
    assert sum(1 for (p, _k) in s1 if p == 20) == 3          # UDP -> controller rules for h1, h2, hb1


def test_new_flow_registered_pinned_and_default(controller):
    c = controller
    dp1 = c.datapaths[1]
    c.handle_new_udp_flow(dp1, 1, "10.0.0.1", "10.0.0.3", 5001, b"x")
    c.handle_new_udp_flow(dp1, 3, "10.0.0.5", "10.0.0.6", 6050, b"x")
    f1 = c.flows[("10.0.0.1", "10.0.0.3", 5001)]
    f2 = c.flows[("10.0.0.5", "10.0.0.6", 6050)]
    assert (f1.path, f1.pinned) == ("A", False) and (f2.path, f2.pinned) == ("B", True)


def test_reroute_is_make_before_break_and_updates_reverse(controller):
    c = controller
    c.handle_new_udp_flow(c.datapaths[1], 1, "10.0.0.1", "10.0.0.3", 5001, b"x")
    FakeDP.log.clear()
    flow = c.flows[("10.0.0.1", "10.0.0.3", 5001)]
    c.move_flow(flow, "B", "reroute", "test")
    fwd = [d for d, m in flowmods(c) if m.priority == 100]
    assert fwd == [3, 4, 1]                                   # s3 (transit), s4, then s1 last
    s1 = [m for d, m in flowmods(c, 1) if m.priority == 100][0]
    assert s1.instructions == [("out", 5)]                    # s1 now sends via Path B port
    rev = [d for d, m in flowmods(c) if m.priority == 90]
    assert sorted(rev) == [1, 3, 4] and flow.path == "B"


def test_path_utilisation_uses_forward_ports_and_missing_is_none(controller):
    import time
    c = controller
    now = time.time()
    assert c.path_utilisation("A", now) is None               # no samples yet: never treated as zero
    c.port_rate[(1, 4)] = (now, 4e6)
    c.port_rate[(2, 2)] = (now, 9e6)
    assert abs(c.path_utilisation("A", now) - 0.9) < 1e-9     # highest link of the path


def test_evaluate_declares_and_reroutes(controller):
    import time
    c = controller
    c.handle_new_udp_flow(c.datapaths[1], 1, "10.0.0.1", "10.0.0.3", 5001, b"x")
    c.handle_new_udp_flow(c.datapaths[1], 3, "10.0.0.5", "10.0.0.6", 6001, b"x")
    c.flows[("10.0.0.1", "10.0.0.3", 5001)].rate_bps = 3e6
    c.flows[("10.0.0.5", "10.0.0.6", 6001)].rate_bps = 8.5e6
    t = time.time()
    for i in range(3):
        c.port_rate[(1, 4)] = (t + i, 10e6); c.port_rate[(2, 2)] = (t + i, 10e6)
        c.port_rate[(1, 5)] = (t + i, 0.0); c.port_rate[(3, 2)] = (t + i, 0.0)
        c._evaluate(t + i)
    assert c.flows[("10.0.0.1", "10.0.0.3", 5001)].path == "B"
    assert c.flows[("10.0.0.5", "10.0.0.6", 6001)].path == "A"   # background stays pinned
