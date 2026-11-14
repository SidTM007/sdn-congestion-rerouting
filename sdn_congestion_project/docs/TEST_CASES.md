# Test cases TC-01 to TC-19: how to run each one and what to capture

All commands run from the project root. "Controller" = Ryu started with `scripts/run_controller.sh`
(or started automatically by `experiments/run_experiment.py`). Scenario runs write everything to `results/<scenario>_<mode>/`.
Capture screenshots / log excerpts as evidence for the System Test Plan.

| TC | What is checked | How to run | Expected result / evidence |
|----|-----------------|------------|----------------------------|
| TC-01 | Topology, link limits | Terminal 1: `scripts/run_controller.sh`. Terminal 2: `sudo python3 topology/topo.py`. In the Mininet CLI: `h3 iperf3 -s -D` then `h1 iperf3 -c 10.0.0.3 -u -b 20M -t 5` | Receiver reports about 10 Mbps (inter-switch limit), `links` shows all links up |
| TC-02 | Default forwarding, loop-free ARP, connectivity | `pingall` in the Mininet CLI; `scripts/dump_flows.sh`; `h1 arp -n` | 0% loss; priority-10 rules forward by destination along Path A; ARP answered by the controller (no storm: CPU idle, few ARP frames in `tcpdump -i s1-eth4 arp`) |
| TC-03 | Sender rate, size, sequence numbers | `python3 -m pytest tests/test_traffic_loopback.py`; or in Mininet `h1 python3 traffic/udp_sender.py --dst-ip 10.0.0.3 --dst-port 5001 --rate-mbps 2 --duration 10` | Achieved rate within 10% of 2 Mbps; receiver sees consecutive sequence numbers |
| TC-04 | Receiver throughput / loss / delay | `h3 python3 traffic/udp_receiver.py --ports 5001 --log rx.csv` while TC-03 runs | `rx.csv` has throughput_mbps, lost_total, loss_pct, delay_ms columns; loss 0% on a clean link |
| TC-05 | Background traffic on demand | `hb2 python3 traffic/udp_receiver.py --ports 6001 6050 &` then `hb1 python3 traffic/background.py --path A --rate-mbps 8 --duration 20` | Start/stop/config appended to `logs/background_runs.csv`; stops by itself, no restart needed; `--path B` uses ports 6050-6099 |
| TC-06 | Statistics polling every 1-2 s | Run any scenario; open `results/.../utilisation.csv` | One row about every 1 s; invalid/missing samples are logged as `invalid_sample` and not treated as zero |
| TC-07 | Single flow, utilisation matches | `sudo -E python3 experiments/run_experiment.py --scenario basic` | `util_A` about 0.40-0.42 for a 4 Mbps flow (payload rate plus headers), within 10% (NFR-005) |
| TC-08 | Concurrent flows add up | `--scenario concurrent` | In `flows.csv` the per-flow rates (2 + 2 + 1 Mbps) sum to the link rate within a small tolerance |
| TC-09 | Sustained load detected | `--scenario heavy --mode adaptive` | `events.csv` has `congestion_declared` for path A; `metrics.json` time_to_detect_s at most 5 s |
| TC-10 | Brief spike ignored | `--scenario spike` | `events.csv` shows `suspect` then `spike_ignored`; no `congestion_declared`, no `reroute` |
| TC-11 | Every decision logged | Any adaptive scenario | `events.csv` lists detections, reroutes, blocked decisions and rebalances with utilisation and reason |
| TC-12 | Reroute: path choice, some flows, flow tables | `bash demo/run_demo.sh` (prints flow tables before and after) or `scripts/dump_flows.sh` before/after in `--scenario heavy` | Heaviest reroutable flows move to Path B; priority-100 rules on s1 change output port 4 to 5; background flows stay |
| TC-13 | Recovery and rebalancing, no restart | `--scenario heavy --mode adaptive` | `metrics.json`: recovery_time_s about 10 s or less; `rebalance` event after the load stops; no component restarted |
| TC-14 | Both paths congested, no flapping | `--scenario hard` | `reroute_blocked` events logged; `min_gap_between_moves_of_same_flow_s` is null or at least 10 |
| TC-15 | Rapid alternating load | `--scenario rapid` | No flow moved twice within 10 s (`min_gap_between_moves_of_same_flow_s` at least 10) |
| TC-16 | Metrics at light / medium / heavy | `--scenario light`, `medium`, `heavy` | `metrics.json`: time to detect, time to reroute, throughput and loss before/during/after; detect and reroute are null at Light |
| TC-17 | Static vs adaptive | `--scenario heavy --mode static --out results/heavy_static`, then `python3 analysis/plot_results.py results/heavy_adaptive --compare results/heavy_static` | `comparison.png`: adaptive shows higher throughput and lower loss during congestion |
| TC-18 | Scripted demo | `bash demo/run_demo.sh` | Congestion turned on live (Path A, then Path B), flow tables before/after, recovery and rebalancing |
| TC-19 | Configuration and invalid input | `python3 -m pytest tests/test_config.py tests/test_udp_proto.py`; `python3 traffic/udp_sender.py --dst-ip 10.0.0.300 --dst-port 5001 --rate-mbps 1 --duration 1` | Clear error messages and exit code 2; configuration values change behaviour without editing code |

Unit tests for the decision logic (TC-07 formula, TC-09, TC-10, TC-12, TC-13, TC-14, TC-15, static mode) run without Mininet:
`python3 -m pytest -q` (49 tests).
