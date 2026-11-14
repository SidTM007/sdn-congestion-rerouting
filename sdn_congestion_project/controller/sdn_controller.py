"""Ryu application: forwarding, ARP handling, statistics polling, congestion detection,
flow rerouting, hold-down and rebalancing (SRS Section 4).

Run (inside the Python 3.8/3.9 virtual environment with Ryu 4.34):
    PYTHONPATH=. ryu-manager controller/sdn_controller.py
Environment variables: SDN_MODE=adaptive|static, SDN_CONFIG=<json file>, SDN_LOG_DIR=<dir>.

Design notes
  * Switch port numbers come from common/topo_spec.py (fixed in topology/topo.py).
  * ARP is answered by the controller itself, so no ARP/broadcast is ever flooded and
    no broadcast storm can form in the two-path topology (FR-054).
  * Default rules forward by destination IP along Path A.  The first UDP packet of every
    flow reaches the controller at s1; the controller installs per-flow rules (priority 100)
    so that flow statistics give OFFERED per-flow rates (FR-024, FR-033).
  * Rerouting installs rules downstream first (s2/s3, then s4) and modifies s1 last, and
    updates the reverse-direction rules (priority 90) (FR-034).
"""
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet, ipv4, udp, arp
from ryu.ofproto import ofproto_v1_3

from common import topo_spec as T
from controller.config import Config
from controller.logic import DecisionEngine, Flow, CsvLog, utilisation

ETH_IP = 0x0800
ETH_ARP = 0x0806
PRIO_MISS, PRIO_DEFAULT, PRIO_UDP_TO_CTRL, PRIO_REVERSE, PRIO_FLOW = 0, 10, 20, 90, 100
FLOW_IDLE_TIMEOUT = 30


class CongestionController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(CongestionController, self).__init__(*args, **kwargs)
        self.cfg = Config.from_env()
        self.engine = DecisionEngine(self.cfg)
        self.datapaths = {}
        self.flows = {}            # key -> Flow
        self.port_prev = {}        # (dpid, port) -> (time, tx_bytes)
        self.port_rate = {}        # (dpid, port) -> (time, bits per second)
        self.flow_prev = {}        # key -> (time, byte_count)
        self.declared_at = {}
        d = self.cfg.log_dir
        self.ev_log = CsvLog(os.path.join(d, "events.csv"), ["time", "event", "path", "util", "details"])
        self.util_log = CsvLog(os.path.join(d, "utilisation.csv"),
                               ["time", "util_A", "util_B", "offered_A_mbps", "offered_B_mbps", "state_A", "state_B", "flows"])
        self.flow_log = CsvLog(os.path.join(d, "flows.csv"), ["time", "src", "dst", "udp_dst", "path", "pinned", "rate_mbps"])
        self.log_event("controller_started", "-", None, "mode=%s config=%s" % (self.cfg.mode, self.cfg.as_dict()))
        self.monitor_thread = hub.spawn(self._monitor_loop)

    # ------------------------------------------------------------------ logging
    def log_event(self, event, path, util, details):
        self.ev_log.row("%.3f" % time.time(), event, path, "" if util is None else "%.3f" % util, details)
        self.logger.info("%s path=%s util=%s %s", event, path, util, details)

    # ------------------------------------------------------------------ helpers
    def add_flow(self, dp, priority, match, actions, idle=0, flags=0):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=priority, match=match, instructions=inst,
                                      idle_timeout=idle, hard_timeout=0, flags=flags))

    def _out(self, dp, port):
        return [dp.ofproto_parser.OFPActionOutput(port)]

    # ------------------------------------------------------------------ switch handshake
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        self.datapaths[dp.id] = dp
        self.install_base_rules(dp)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif dp.id in self.datapaths and ev.state == DEAD_DISPATCHER:
            del self.datapaths[dp.id]
            self.log_event("switch_disconnected", "-", None, "dpid=%s" % dp.id)

    def install_base_rules(self, dp):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        # table miss -> controller (ARP and anything unexpected)
        self.add_flow(dp, PRIO_MISS, parser.OFPMatch(),
                      [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])
        # default forwarding by destination IP along Path A (FR-005)
        for name, h in T.HOSTS.items():
            out = T.out_port_toward(dp.id, name, "A")
            self.add_flow(dp, PRIO_DEFAULT, parser.OFPMatch(eth_type=ETH_IP, ipv4_dst=h["ip"]), self._out(dp, out))
        # s1: first packet of every UDP flow from a sender host goes to the controller
        if dp.id == 1:
            for name, h in T.HOSTS.items():
                if h["dpid"] == 1:
                    self.add_flow(dp, PRIO_UDP_TO_CTRL,
                                  parser.OFPMatch(in_port=h["port"], eth_type=ETH_IP, ip_proto=17),
                                  [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)])
        self.log_event("base_rules_installed", "-", None, "dpid=%s" % dp.id)

    # ------------------------------------------------------------------ packet-in
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dp = msg.datapath
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        arp_pkt = pkt.get_protocol(arp.arp)
        if arp_pkt is not None:
            self.handle_arp(dp, in_port, eth, arp_pkt)
            return
        ip4 = pkt.get_protocol(ipv4.ipv4)
        u = pkt.get_protocol(udp.udp)
        if ip4 is not None and u is not None and dp.id == 1:
            self.handle_new_udp_flow(dp, in_port, ip4.src, ip4.dst, u.dst_port, msg.data)

    def handle_arp(self, dp, in_port, eth, arp_pkt):
        """Answer ARP requests for known hosts directly; never flood (FR-054)."""
        if arp_pkt.opcode != arp.ARP_REQUEST:
            return
        name = T.HOST_BY_IP.get(arp_pkt.dst_ip)
        if name is None:
            return
        mac = T.HOSTS[name]["mac"]
        reply = packet.Packet()
        reply.add_protocol(ethernet.ethernet(ethertype=ETH_ARP, dst=eth.src, src=mac))
        reply.add_protocol(arp.arp(opcode=arp.ARP_REPLY, src_mac=mac, src_ip=arp_pkt.dst_ip,
                                   dst_mac=arp_pkt.src_mac, dst_ip=arp_pkt.src_ip))
        reply.serialize()
        ofp, parser = dp.ofproto, dp.ofproto_parser
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=ofp.OFP_NO_BUFFER, in_port=ofp.OFPP_CONTROLLER,
                                        actions=[parser.OFPActionOutput(in_port)], data=reply.data))

    def handle_new_udp_flow(self, dp, in_port, src_ip, dst_ip, dport, data):
        """First packet of a UDP flow: register the flow, install per-flow rules, forward the packet."""
        if src_ip not in T.HOST_BY_IP or dst_ip not in T.HOST_BY_IP:
            return
        key = (src_ip, dst_ip, dport)
        flow = self.flows.get(key)
        if flow is None:
            kind, pinned_path = T.classify_port(dport)
            path = pinned_path or "A"
            flow = Flow(key=key, home=path if pinned_path else "A", path=path, pinned=pinned_path is not None)
            self.flows[key] = flow
            self.install_flow_rules(flow, path, initial=True)
            self.log_event("flow_registered", path, None, "%s->%s udp_dst=%s pinned=%s" % (src_ip, dst_ip, dport, flow.pinned))
        ofp, parser = dp.ofproto, dp.ofproto_parser
        out = T.out_port_toward(1, T.HOST_BY_IP[dst_ip], flow.path)
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=ofp.OFP_NO_BUFFER, in_port=in_port,
                                        actions=[parser.OFPActionOutput(out)], data=data))

    @set_ev_cls(ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER)
    def flow_removed_handler(self, ev):
        m = ev.msg.match
        key = (m.get("ipv4_src"), m.get("ipv4_dst"), m.get("udp_dst"))
        if ev.msg.datapath.id == 1 and self.flows.pop(key, None) is not None:
            self.flow_prev.pop(key, None)
            self.log_event("flow_expired", "-", None, "%s->%s udp_dst=%s idle" % key)

    # ------------------------------------------------------------------ rule installation
    def install_flow_rules(self, flow, path, initial=False):
        """Make-before-break (FR-034): downstream switches first (s2 or s3, then s4), s1 last;
        then the reverse-direction rules so return traffic is consistent."""
        src_ip, dst_ip, dport = flow.key
        src, dst = T.HOST_BY_IP[src_ip], T.HOST_BY_IP[dst_ip]
        transit = T.TRANSIT[path]
        for dpid in (transit, 4, 1):
            dp = self.datapaths.get(dpid)
            if dp is None:
                continue
            parser, ofp = dp.ofproto_parser, dp.ofproto
            match = parser.OFPMatch(eth_type=ETH_IP, ipv4_src=src_ip, ipv4_dst=dst_ip, ip_proto=17, udp_dst=dport)
            flags = ofp.OFPFF_SEND_FLOW_REM if (dpid == 1 and not flow.pinned) else 0
            self.add_flow(dp, PRIO_FLOW, match, self._out(dp, T.out_port_toward(dpid, dst, path)),
                          idle=0 if flow.pinned else FLOW_IDLE_TIMEOUT, flags=flags)
        if not (initial and path == "A"):   # Path A reverse traffic is covered by the default rules
            for dpid in (1, transit, 4):
                dp = self.datapaths.get(dpid)
                if dp is None:
                    continue
                parser = dp.ofproto_parser
                match = parser.OFPMatch(eth_type=ETH_IP, ipv4_src=dst_ip, ipv4_dst=src_ip)
                self.add_flow(dp, PRIO_REVERSE, match, self._out(dp, T.out_port_toward(dpid, src, path)))

    def move_flow(self, flow, new_path, kind, reason):
        old = flow.path
        self.install_flow_rules(flow, new_path)
        flow.path = new_path
        self.flow_prev.pop(flow.key, None)   # entry was replaced, counters restart
        self.log_event(kind, "%s->%s" % (old, new_path), None,
                       "flow %s->%s udp_dst=%s rate=%.2f Mbps; %s" % (flow.key[0], flow.key[1], flow.key[2], flow.rate_bps / 1e6, reason))

    # ------------------------------------------------------------------ statistics
    def _monitor_loop(self):
        hub.sleep(2)
        while True:
            self._request_stats()
            hub.sleep(self.cfg.poll_interval_s * 0.5)
            try:
                self._evaluate(time.time())
            except Exception:
                self.logger.exception("evaluation failed")
            hub.sleep(self.cfg.poll_interval_s * 0.5)

    def _request_stats(self):
        for dpid, dp in list(self.datapaths.items()):
            parser, ofp = dp.ofproto_parser, dp.ofproto
            dp.send_msg(parser.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))
            if dpid == 1:
                dp.send_msg(parser.OFPFlowStatsRequest(dp))

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        now, dpid = time.time(), ev.msg.datapath.id
        for st in ev.msg.body:
            key = (dpid, st.port_no)
            prev = self.port_prev.get(key)
            self.port_prev[key] = (now, st.tx_bytes)
            if prev is not None:
                u = utilisation(st.tx_bytes, prev[1], now - prev[0], 1.0)   # capacity 1 bps -> bits per second
                if u is not None:
                    self.port_rate[key] = (now, u)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        now = time.time()
        for st in ev.msg.body:
            if st.priority != PRIO_FLOW:
                continue
            m = st.match
            key = (m.get("ipv4_src"), m.get("ipv4_dst"), m.get("udp_dst"))
            flow = self.flows.get(key)
            if flow is None:
                continue
            prev = self.flow_prev.get(key)
            self.flow_prev[key] = (now, st.byte_count)
            if prev is None:
                continue
            dt = now - prev[0]
            delta = st.byte_count - prev[1] if st.byte_count >= prev[1] else st.byte_count  # counters restart after a move
            if dt > 0:
                flow.rate_bps = delta * 8.0 / dt

    def path_utilisation(self, path, now):
        """Highest utilisation among the forward-direction links of the path (FR-023)."""
        cap = self.cfg.capacity_bps
        vals = []
        for key in T.FORWARD_PORTS[path]:
            rec = self.port_rate.get(key)
            if rec is None or now - rec[0] > 2.5 * self.cfg.poll_interval_s:
                return None          # missing sample: never treated as zero traffic
            vals.append(rec[1] / cap)
        return max(vals) if vals else None

    def _evaluate(self, now):
        util = {p: self.path_utilisation(p, now) for p in T.PATHS}
        flows = list(self.flows.values())
        events, moves = self.engine.step(now, util, flows)
        for name, path, u, details in events:
            self.log_event(name, path, u, details)
        for mv in moves:
            if mv["kind"] == "reroute":
                self.log_event("flow_table_before", "-", None, "use scripts/dump_flows.sh for the full table")
            self.move_flow(mv["flow"], mv["to"], mv["kind"], mv["reason"])
        offered = {p: sum(f.rate_bps for f in flows if f.path == p) / 1e6 for p in T.PATHS}
        st = self.engine.states()
        self.util_log.row("%.3f" % now, "" if util["A"] is None else "%.4f" % util["A"],
                          "" if util["B"] is None else "%.4f" % util["B"],
                          "%.3f" % offered["A"], "%.3f" % offered["B"], st["A"], st["B"], len(flows))
        for f in flows:
            self.flow_log.row("%.3f" % now, f.key[0], f.key[1], f.key[2], f.path, int(f.pinned), "%.3f" % (f.rate_bps / 1e6))
