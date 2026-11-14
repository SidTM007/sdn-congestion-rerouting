"""Controller configuration (SRS Section 6.2 threshold and timer table).

All values are configurable without editing source code: pass a JSON file via the
SDN_CONFIG environment variable, and/or SDN_MODE / SDN_LOG_DIR.
"""
import json
import os
from dataclasses import dataclass, asdict


@dataclass
class Config:
    link_capacity_mbps: float = 10.0     # capacity of each inter-switch link
    poll_interval_s: float = 1.0         # statistics polling interval (1-2 s)
    congested_threshold: float = 0.80    # path is over-loaded above 80%
    detection_window: int = 3            # consecutive samples above threshold
    clear_threshold: float = 0.50        # path is clear below 50% ...
    clear_window: int = 5                # ... for this many consecutive samples
    hold_down_s: float = 10.0            # per-flow minimum time between moves
    alternate_max_util: float = 0.70     # never move onto a path above this
    mode: str = "adaptive"               # "adaptive" or "static" (baseline)
    log_dir: str = "logs"

    def validate(self):
        if self.link_capacity_mbps <= 0:
            raise ValueError("link_capacity_mbps must be > 0")
        if not 1.0 <= self.poll_interval_s <= 2.0:
            raise ValueError("poll_interval_s must be between 1 and 2 seconds")
        if not 0 < self.clear_threshold < self.congested_threshold <= 1.0:
            raise ValueError("need 0 < clear_threshold < congested_threshold <= 1")
        if not 0 < self.alternate_max_util <= 1.0:
            raise ValueError("alternate_max_util must be in (0, 1]")
        if int(self.detection_window) < 1 or int(self.clear_window) < 1:
            raise ValueError("detection_window and clear_window must be >= 1")
        if self.hold_down_s < 0:
            raise ValueError("hold_down_s must be >= 0")
        if self.mode not in ("adaptive", "static"):
            raise ValueError("mode must be 'adaptive' or 'static'")
        return self

    @property
    def capacity_bps(self):
        return self.link_capacity_mbps * 1e6

    @classmethod
    def from_env(cls):
        cfg = cls()
        path = os.environ.get("SDN_CONFIG")
        if path:
            with open(path) as fh:
                data = json.load(fh)
            for key, value in data.items():
                if not hasattr(cfg, key):
                    raise ValueError("unknown configuration key: %s" % key)
                setattr(cfg, key, value)
        if os.environ.get("SDN_MODE"):
            cfg.mode = os.environ["SDN_MODE"]
        if os.environ.get("SDN_LOG_DIR"):
            cfg.log_dir = os.environ["SDN_LOG_DIR"]
        return cfg.validate()

    def as_dict(self):
        return asdict(self)
