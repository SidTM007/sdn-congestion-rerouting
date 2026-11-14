# Congestion Detection and Traffic Rerouting Using UDP (SDN, Mininet, Ryu)

Team 4 mini-project (SDN - Socket Programming). UDP traffic flows over a Mininet network with two parallel
10 Mbps paths. A Ryu (OpenFlow 1.3) controller polls port and flow statistics every second, computes link
utilisation, detects sustained congestion (above 80% for 3 consecutive samples), and moves some flows to the
less-loaded path by updating flow rules, without restarting anything. A per-flow hold-down timer and
hysteresis prevent endless flipping. Static routing is the baseline for comparison.
The behaviour follows the SRS (Version 1.1), requirements FR-001 to FR-054.

## Layout

```
common/        topo_spec.py (topology, ports, port ranges), udp_proto.py (payload format, validation)
topology/      topo.py                 Mininet topology (6 hosts, 4 switches, Path A and Path B)
controller/    sdn_controller.py       Ryu app (forwarding, ARP, stats, detection, reroute, rebalance)
               logic.py                pure decision logic (state machine, planner, hold-down), unit-tested
               config.py               thresholds and timers (SRS section 6.2)
traffic/       udp_sender.py, udp_receiver.py, background.py
experiments/   run_experiment.py (scenarios), run_all.sh (full matrix)
analysis/      metrics.py (time to detect/reroute, recovery, throughput, loss), plot_results.py (graphs)
demo/          run_demo.sh             scripted live demo incl. the hard scenario
scripts/       setup_system.sh, setup_controller_env.sh, run_controller.sh, dump_flows.sh
tests/         49 tests (no Mininet needed)
docs/          TEST_CASES.md           how to run TC-01 ... TC-19
```

Test evidence (screenshots, logs, metrics) goes in `TEST_EVIDENCE/` after the Mininet runs.

## Topology

```
h1, h2, hb1 --- s1 ==== s2 ==== s4 --- h3, h4, hb2        Path A (main):      s1 - s2 - s4
                 \                /
                  ==== s3 =======                          Path B (alternate): s1 - s3 - s4
```
Inter-switch links: 10 Mbps, 5 ms delay, 100-packet queue (so loss appears under overload). Host links: 100 Mbps.
Hosts: h1 10.0.0.1, h2 .2, h3 .3, h4 .4, hb1 .5, hb2 .6. Controller: 127.0.0.1:6653.

## Setup (Ubuntu 22.04)

```bash
bash scripts/setup_system.sh                 # mininet, openvswitch, iperf3, matplotlib, pytest
# Ryu 4.34 needs Python 3.8 or 3.9 (Ubuntu 22.04 ships 3.10), so use a separate virtual environment:
sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt update && sudo apt install python3.9 python3.9-venv
bash scripts/setup_controller_env.sh         # creates ~/sdn-venv with ryu==4.34 and eventlet==0.30.2
export RYU_MANAGER=~/sdn-venv/bin/ryu-manager
```
Mininet and the traffic/analysis scripts use the system Python; only the controller runs in the venv.

## Quick start

1. Unit tests (no network needed): `python3 -m pytest -q`
2. Manual run, two terminals:
   ```bash
   # terminal 1 (venv): controller
   source ~/sdn-venv/bin/activate && bash scripts/run_controller.sh adaptive
   # terminal 2: topology with CLI
   sudo python3 topology/topo.py
   mininet> h3 python3 traffic/udp_receiver.py --ports 5001 --log rx.csv &
   mininet> h1 python3 traffic/udp_sender.py --dst-ip 10.0.0.3 --dst-port 5001 --rate-mbps 2 --duration 30
   ```
3. Scenario runs (starts the controller itself): `sudo -E env RYU_MANAGER=$RYU_MANAGER python3 experiments/run_experiment.py --scenario heavy --mode adaptive`
4. Whole matrix + graphs: `bash experiments/run_all.sh`
5. Live demo: `bash demo/run_demo.sh`
6. Clean up after a crash: `sudo mn -c`

Scenarios: `basic`, `concurrent`, `spike`, `light`, `medium`, `heavy`, `hard`, `rapid`, `demo`.
Each run writes `events.csv` (every decision), `utilisation.csv`, `flows.csv`, receiver CSVs and `metrics.json`
to `results/<scenario>_<mode>/`. Use `scripts/dump_flows.sh` to show flow tables before and after a reroute.

## How it works

* **Utilisation** = (byte-counter delta x 8) / elapsed seconds / link capacity. Path utilisation is the highest
  utilisation among the forward-direction inter-switch links of the path (Path A: s1 port 4 and s2 port 2;
  Path B: s1 port 5 and s3 port 2). A missing or reset sample is logged and never treated as zero traffic.
* **Detection:** path states NORMAL, SUSPECT, CONGESTED. Congested after `detection_window` (3) consecutive samples
  above `congested_threshold` (80%); a shorter spike is ignored. A path is clear again after `clear_window` (5)
  samples below `clear_threshold` (50%) (hysteresis).
* **Offered load:** port counters cannot exceed 100% of capacity, so the controller plans with offered per-flow
  rates from flow statistics (per-flow rules at s1, installed when the first UDP packet of a flow arrives).
* **Rerouting:** heaviest reroutable flows first, only as many as needed, never onto a path above
  `alternate_max_util` (70%) or one that is itself congested. Rules are installed downstream first (s2/s3, then s4),
  s1 last, and the reverse-direction rules are updated.
* **Hold-down:** a per-flow timer (`hold_down_s` = 10 s). **Rebalancing:** when a path is clear and a flow's hold-down
  has expired, the flow returns home only if the projected utilisation stays below the congested threshold.
* **ARP / loops (FR-054):** the controller answers ARP itself and never floods, so the two-path topology has no broadcast storm.
* **Background traffic** uses reserved ports: 6001-6049 pinned to Path A, 6050-6099 pinned to Path B, never rerouted.
  Application flows use ports 5001-5099 (several flows per host pair are allowed).
* **Static baseline:** `--mode static` keeps all monitoring but never reroutes.

## Configuration

Defaults are in `controller/config.py` (SRS section 6.2). Override with a JSON file
(`SDN_CONFIG=my.json`, for example `{"hold_down_s": 15, "congested_threshold": 0.85}`) or pass `--config my.json`
to `run_experiment.py`. `SDN_MODE=static|adaptive` and `SDN_LOG_DIR=<dir>` are environment variables.
Invalid values are rejected at start-up with a clear error.

## Metric definitions

Time to detect: start of background load until `congestion_declared`. Time to reroute: `congestion_declared` until
the rules are installed (`time_to_reroute_install_s`) and until Path B first shows the moved traffic
(`time_to_reroute_traffic_s`, about 1 s resolution). Recovery time: start of congestion until application throughput
is back to at least 90% of its pre-congestion mean.

## Verification status and notes

* The decision logic, traffic tools, configuration and the controller's rule/ordering code are covered by 49 automated tests
  (the controller test uses stand-ins for Ryu). These tests pass.
* The Mininet / Open vSwitch / Ryu parts must be run on your Ubuntu machine: expect to check port numbers with
  `sudo ovs-vsctl show`, controller connection (`ryu-manager` log) and flow tables with `scripts/dump_flows.sh`.
  If something differs, `events.csv` and `controller.out` show what the controller did.
* Link emulation is approximate (software queues and timers), so measured utilisation can differ a few percent
  from the configured rates; the configured sender rate counts UDP payload bytes (headers add about 4% at 1000-byte packets).
* Troubleshooting: `ImportError ... ALREADY_HANDLED` means eventlet is not 0.30.2; switches not connecting means
  the controller is not on port 6653 or `sudo mn -c` has not been run; no UDP flows in `flows.csv` means the first packet
  did not reach the controller (check the priority-20 rules on s1).
