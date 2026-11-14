"""TC-03/TC-04 on the loopback interface: sender rate and sequence numbers, receiver throughput/loss/delay."""
import csv
import threading
import time

from traffic.udp_receiver import run_receiver
from traffic.udp_sender import run_sender


def test_sender_receiver_loopback(tmp_path):
    log = tmp_path / "rx.csv"
    result = {}
    stop = threading.Event()
    th = threading.Thread(target=lambda: result.update(run_receiver([15001], 1.0, None, str(log), "127.0.0.1", True, stop)))
    th.start()
    time.sleep(0.3)
    stats = run_sender("127.0.0.1", 15001, 2.0, 1000, 4.0, flow_id=3, quiet=True)
    time.sleep(0.5)
    stop.set()
    th.join()
    assert abs(stats["achieved_mbps"] - 2.0) / 2.0 < 0.10            # sender within 10% of configured rate
    key = "3:15001"
    assert key in result and result[key]["lost"] == 0 and result[key]["received"] == stats["packets"]
    rows = list(csv.DictReader(open(log)))
    assert rows and max(float(r["throughput_mbps"]) for r in rows) > 1.5
    assert float(rows[-1]["delay_ms_avg"]) < 50
