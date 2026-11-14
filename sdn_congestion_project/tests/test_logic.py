"""Unit and simulation tests for the decision logic: TC-07 (utilisation), TC-09/10 (detection, spike),
TC-11 (decision log events), TC-12 (reroute choice), TC-13 (rebalance), TC-14/15 (no flapping), static mode."""
from controller.config import Config
from controller.logic import (DecisionEngine, Flow, PathMonitor, HoldDown, utilisation, plan_reroute,
                              plan_rebalance, NORMAL, SUSPECT, CONGESTED)


def cfg(**kw):
    c = Config()
    for k, v in kw.items():
        setattr(c, k, v)
    return c.validate()


# ---------------- utilisation (TC-07) ----------------
def test_utilisation_formula():
    # 1,250,000 bytes in 1 s = 10 Mbit/s = 100% of a 10 Mbps link
    assert utilisation(1_250_000, 0, 1.0, 10e6) == 1.0
    assert abs(utilisation(500_000, 0, 1.0, 10e6) - 0.4) < 1e-9


def test_invalid_samples_are_not_zero_traffic():
    assert utilisation(10, 100, 1.0, 10e6) is None      # counter reset
    assert utilisation(100, 10, 0.0, 10e6) is None      # no elapsed time
    m = PathMonitor("A", cfg())
    assert m.update(None)[0][0] == "invalid_sample" and m.state == NORMAL


# ---------------- detection (TC-09, TC-10) ----------------
def test_spike_shorter_than_window_is_ignored():
    m = PathMonitor("A", cfg())
    names = [e[0] for u in (0.95, 0.95, 0.3, 0.3) for e in m.update(u)]
    assert "congestion_declared" not in names and "spike_ignored" in names and m.state == NORMAL


def test_sustained_load_declared_after_window():
    m = PathMonitor("A", cfg())
    seq = [[e[0] for e in m.update(0.95)] for _ in range(3)]
    assert seq == [["suspect"], [], ["congestion_declared"]] and m.state == CONGESTED


def test_exactly_threshold_is_not_over():
    m = PathMonitor("A", cfg())
    for _ in range(5):
        m.update(0.80)
    assert m.state == NORMAL


def test_hysteresis_clear_needs_five_samples_below_50():
    m = PathMonitor("A", cfg())
    for _ in range(3):
        m.update(0.95)
    for _ in range(4):
        m.update(0.40)
    assert m.state == CONGESTED
    m.update(0.60)                      # between thresholds resets the clear counter
    for _ in range(4):
        m.update(0.40)
    assert m.state == CONGESTED
    assert [e[0] for e in m.update(0.40)] == ["cleared"] and m.state == NORMAL


# ---------------- planning (TC-12) ----------------
def flows(*rates, path="A"):
    return [Flow(("10.0.0.%d" % (i + 1), "10.0.0.3", 5001 + i), "A", path, False, r * 1e6) for i, r in enumerate(rates)]


def test_heaviest_flow_moved_first_and_only_as_many_as_needed():
    c = cfg()
    fl = flows(1.0, 3.0, 2.0) + [Flow(("10.0.0.5", "10.0.0.6", 6001), "A", "A", True, 4.0e6)]   # offered 10 Mbps
    chosen, _ = plan_reroute(fl, "A", "B", 0, c, HoldDown(10))
    assert [f.rate_bps for f in chosen] == [3.0e6]       # 10 - 3 = 7 <= 8 (80%), so one flow is enough


def test_pinned_flows_never_moved():
    c = cfg()
    fl = [Flow(("10.0.0.5", "10.0.0.6", 6001), "A", "A", True, 11e6)]
    chosen, reason = plan_reroute(fl, "A", "B", 0, c, HoldDown(10))
    assert not chosen and "pinned" in reason


def test_alternate_limit_respected():
    c = cfg()
    fl = flows(5.0, 5.0, 3.0) + flows(4.0, path="B")[:1]          # B already carries 4 Mbps, limit 7 Mbps
    fl[3].key = ("10.0.0.9", "10.0.0.4", 5009)
    chosen, _ = plan_reroute(fl, "A", "B", 0, c, HoldDown(10))
    assert sum(f.rate_bps for f in chosen) + 4e6 <= 7e6 + 1


def test_hold_down_blocks_flows():
    c = cfg()
    h = HoldDown(10)
    fl = flows(6.0, 6.0)
    for f in fl:
        h.mark(f.key, 100)
    chosen, reason = plan_reroute(fl, "A", "B", 105, c, h)
    assert not chosen and "hold-down" in reason
    chosen, _ = plan_reroute(fl, "A", "B", 111, c, h)
    assert chosen


def test_rebalance_only_if_projection_stays_below_threshold():
    c = cfg()
    on_b = flows(3.0, path="B")
    assert plan_rebalance(on_b, "A", "B", 0, c, HoldDown(10)) == on_b
    bg = [Flow(("10.0.0.5", "10.0.0.6", 6001), "A", "A", True, 6.0e6)] + on_b      # 6 + 3 = 9 > 8
    assert plan_rebalance(bg, "A", "B", 0, c, HoldDown(10)) == []


# ---------------- whole-engine simulations ----------------
def simulate(c, bg_schedule, seconds, flow_rates=(1.5, 1.5)):
    """bg_schedule(t) -> (bgA_mbps, bgB_mbps). Offered load drives measured utilisation (capped at 100%)."""
    eng = DecisionEngine(c)
    fl = flows(*flow_rates)
    bgA = Flow(("10.0.0.5", "10.0.0.6", 6001), "A", "A", True, 0)
    bgB = Flow(("10.0.0.5", "10.0.0.6", 6050), "B", "B", True, 0)
    allf = fl + [bgA, bgB]
    log, mv_times = [], {}
    for t in range(seconds):
        bgA.rate_bps, bgB.rate_bps = [x * 1e6 for x in bg_schedule(t)]
        off = {p: sum(f.rate_bps for f in allf if f.path == p) / c.capacity_bps for p in "AB"}
        util = {p: min(1.0, off[p]) for p in "AB"}
        events, moves = eng.step(float(t), util, allf)
        for e in events:
            log.append((t,) + tuple(e))
        for m in moves:
            m["flow"].path = m["to"]
            mv_times.setdefault(m["flow"].key, []).append((t, m["kind"], m["to"]))
    return eng, log, mv_times, fl


def test_heavy_load_detect_reroute_recover_and_rebalance():          # TC-09, 12, 13
    c = cfg()
    eng, log, mv, fl = simulate(c, lambda t: (8.5, 0) if 5 <= t < 40 else (0, 0), 80)
    declared = [e for e in log if e[1] == "congestion_declared" and e[2] == "A"]
    assert declared and declared[0][0] == 7                          # 3 samples above 80% starting at t=5
    assert all(any(k == "reroute" and to == "B" for _, k, to in mv[f.key]) for f in fl)
    assert all(f.path == "A" for f in fl)                            # rebalanced home after the load stopped
    assert all(any(k == "rebalance" for _, k, _t in mv[f.key]) for f in fl)


def test_light_and_brief_spike_cause_no_moves():                     # TC-10
    c = cfg()
    _, log, mv, _ = simulate(c, lambda t: (8.5, 0) if t in (5, 6) else (0.5, 0), 40)
    assert not mv and not [e for e in log if e[1] == "congestion_declared"]


def test_both_paths_congested_no_flipping():                         # TC-14
    c = cfg()
    _, log, mv, _ = simulate(c, lambda t: (8.5, 8.5) if t >= 5 else (0, 0), 90)
    assert not any(k == "reroute" and t > 40 for v in mv.values() for t, k, _to in v)
    assert [e for e in log if e[1] == "reroute_blocked"]            # blocked decisions are logged


def test_rapid_alternating_load_respects_hold_down():                # TC-15
    c = cfg()
    _, _, mv, _ = simulate(c, lambda t: ((8.5, 0) if (t // 6) % 2 == 0 else (0, 8.5)) if t >= 5 else (0, 0), 150)
    for times in mv.values():
        gaps = [b[0] - a[0] for a, b in zip(times, times[1:])]
        assert all(g >= c.hold_down_s for g in gaps), gaps


def test_static_mode_never_moves_flows():                            # baseline (TC-17)
    c = cfg(mode="static")
    _, log, mv, _ = simulate(c, lambda t: (8.5, 0) if t >= 5 else (0, 0), 60)
    assert not mv and [e for e in log if e[1] == "reroute_suppressed"]
