"""Pure decision logic (no Ryu / OpenFlow imports) so it can be unit-tested.

Implements SRS Section 4: utilisation, congestion detection (threshold + window),
alternate-path selection, selecting flows to move, per-flow hold-down, hysteresis
and rebalancing.  State machine per path: NORMAL -> SUSPECT -> CONGESTED -> NORMAL.
"""
import csv
import os
from dataclasses import dataclass

NORMAL, SUSPECT, CONGESTED = "NORMAL", "SUSPECT", "CONGESTED"
OTHER = {"A": "B", "B": "A"}


def utilisation(bytes_now, bytes_prev, dt, capacity_bps):
    """(bytes delta x 8) / dt / capacity.  Returns None for an invalid sample
    (counter reset, no elapsed time) so it is never treated as zero traffic (BR-7)."""
    if dt is None or dt <= 0 or capacity_bps <= 0 or bytes_now < bytes_prev:
        return None
    return (bytes_now - bytes_prev) * 8.0 / dt / capacity_bps


@dataclass
class Flow:
    key: tuple              # (src_ip, dst_ip, udp_dst)
    home: str               # default path ("A")
    path: str               # path currently used
    pinned: bool            # background flows are never rerouted
    rate_bps: float = 0.0   # offered rate measured from flow statistics


class HoldDown:
    """Per-flow hold-down timer (a flow moved less than hold_down_s ago is not moved again)."""

    def __init__(self, hold_down_s):
        self.hold_down_s = hold_down_s
        self.last_move = {}

    def ok(self, key, now):
        t = self.last_move.get(key)
        return t is None or (now - t) >= self.hold_down_s

    def mark(self, key, now):
        self.last_move[key] = now


class PathMonitor:
    """Per-path state machine with threshold, detection window and hysteresis."""

    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.state = NORMAL
        self.over = 0
        self.clear = 0

    def update(self, util):
        """Feed one utilisation sample (fraction or None). Returns [(event, details)]."""
        cfg, ev = self.cfg, []
        if util is None:
            return [("invalid_sample", "missing or invalid statistics sample; state unchanged")]
        thr, clr = cfg.congested_threshold, cfg.clear_threshold
        if self.state == NORMAL:
            if util > thr:
                self.over = 1
                if self.over >= cfg.detection_window:
                    self.state, self.clear = CONGESTED, 0
                    ev.append(("congestion_declared", "utilisation above %.0f%% for %d sample(s)" % (thr * 100, self.over)))
                else:
                    self.state = SUSPECT
                    ev.append(("suspect", "utilisation above %.0f%%, counting samples" % (thr * 100)))
        elif self.state == SUSPECT:
            if util > thr:
                self.over += 1
                if self.over >= cfg.detection_window:
                    self.state, self.clear = CONGESTED, 0
                    ev.append(("congestion_declared", "utilisation above %.0f%% for %d consecutive samples" % (thr * 100, self.over)))
            else:
                self.state, self.over = NORMAL, 0
                ev.append(("spike_ignored", "utilisation fell back before the detection window ended"))
        else:  # CONGESTED
            if util > thr:
                self.clear = 0
            elif util < clr:
                self.clear += 1
                if self.clear >= cfg.clear_window:
                    self.state, self.over, self.clear = NORMAL, 0, 0
                    ev.append(("cleared", "utilisation below %.0f%% for %d samples" % (clr * 100, cfg.clear_window)))
            else:
                self.clear = 0
        return ev


def plan_reroute(flows, src, dst, now, cfg, hold):
    """Choose flows to move from congested path `src` to `dst` (FR: heaviest first, only as many
    as needed, projected utilisation from OFFERED per-flow rates, never above the alternate limit).
    Returns (chosen_flows, reason_if_none)."""
    cap = cfg.capacity_bps
    thr, lim = cfg.congested_threshold * cap, cfg.alternate_max_util * cap
    src_off = sum(f.rate_bps for f in flows if f.path == src)
    dst_off = sum(f.rate_bps for f in flows if f.path == dst)
    if src_off <= thr:
        return [], "projected utilisation of path %s already at or below the threshold" % src
    movable = [f for f in flows if f.path == src and not f.pinned]
    if not movable:
        return [], "no reroutable flows on path %s (only pinned background traffic)" % src
    eligible = sorted([f for f in movable if hold.ok(f.key, now)], key=lambda f: -f.rate_bps)
    if not eligible:
        return [], "hold-down active for all reroutable flows on path %s" % src
    chosen = []
    for f in eligible:
        if src_off <= thr:
            break
        if dst_off + f.rate_bps > lim:
            continue
        chosen.append(f)
        src_off -= f.rate_bps
        dst_off += f.rate_bps
    if not chosen:
        return [], "moving any flow would push path %s above the %.0f%% alternate-path limit" % (dst, cfg.alternate_max_util * 100)
    return chosen, ""


def plan_rebalance(flows, home, away, now, cfg, hold):
    """Move flows back to `home` only if its projected utilisation stays below the congested threshold."""
    cap = cfg.capacity_bps
    thr = cfg.congested_threshold * cap
    home_off = sum(f.rate_bps for f in flows if f.path == home)
    cand = sorted([f for f in flows if f.home == home and f.path == away and not f.pinned and hold.ok(f.key, now)],
                  key=lambda f: -f.rate_bps)
    chosen = []
    for f in cand:
        if home_off + f.rate_bps < thr:
            chosen.append(f)
            home_off += f.rate_bps
    return chosen


class DecisionEngine:
    """One call to step() per statistics sample. Returns (events, moves)."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.monitors = {"A": PathMonitor("A", cfg), "B": PathMonitor("B", cfg)}
        self.hold = HoldDown(cfg.hold_down_s)
        self._last_blocked = {}

    def states(self):
        return {p: m.state for p, m in self.monitors.items()}

    def _blocked(self, events, path, util, reason):
        if self._last_blocked.get(path) != reason:
            self._last_blocked[path] = reason
            events.append(("reroute_blocked", path, util, reason))

    def step(self, now, util, flows):
        """util: {'A': fraction|None, 'B': fraction|None}; flows: list[Flow] with offered rates."""
        cfg, events, moves = self.cfg, [], []
        for p in ("A", "B"):
            for name, details in self.monitors[p].update(util[p]):
                events.append((name, p, util[p], details))
            if self.monitors[p].state != CONGESTED:
                self._last_blocked.pop(p, None)
        thr = cfg.congested_threshold
        if cfg.mode != "adaptive":
            for e in list(events):
                if e[0] == "congestion_declared":
                    events.append(("reroute_suppressed", e[1], e[2], "static routing mode: no rerouting (baseline)"))
            return events, moves
        moved = set()
        for p in ("A", "B"):
            q = OTHER[p]
            if self.monitors[p].state == CONGESTED and util[p] is not None and util[p] > thr:
                alt_ok = util[q] is not None and util[q] < cfg.alternate_max_util and self.monitors[q].state != CONGESTED
                if not alt_ok:
                    self._blocked(events, p, util[p], "alternate path %s not eligible (utilisation %s, state %s)" % (
                        q, "n/a" if util[q] is None else "%.0f%%" % (util[q] * 100), self.monitors[q].state))
                    continue
                chosen, reason = plan_reroute(flows, p, q, now, cfg, self.hold)
                if not chosen:
                    self._blocked(events, p, util[p], reason)
                    continue
                self._last_blocked.pop(p, None)
                for f in chosen:
                    self.hold.mark(f.key, now)
                    moved.add(f.key)
                    moves.append(dict(flow=f, to=q, kind="reroute",
                                      reason="path %s congested (%.0f%%); path %s at %.0f%%" % (p, util[p] * 100, q, util[q] * 100)))
        for home in ("A", "B"):
            away = OTHER[home]
            if self.monitors[home].state == NORMAL:
                pool = [f for f in flows if f.key not in moved]
                for f in plan_rebalance(pool, home, away, now, cfg, self.hold):
                    self.hold.mark(f.key, now)
                    moves.append(dict(flow=f, to=home, kind="rebalance",
                                      reason="path %s clear; projected utilisation stays below %.0f%%" % (home, thr * 100)))
        return events, moves


class CsvLog:
    """Tiny CSV writer that flushes every row (so logs survive a killed controller)."""

    def __init__(self, path, header):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._fh = open(path, "w", newline="")
        self._w = csv.writer(self._fh)
        self._w.writerow(header)
        self._fh.flush()

    def row(self, *values):
        self._w.writerow(values)
        self._fh.flush()
