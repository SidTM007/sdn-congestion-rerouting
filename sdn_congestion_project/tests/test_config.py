"""TC-19: configuration values are applied and invalid values are rejected."""
import json
import pytest
from controller.config import Config


def test_defaults_match_srs():
    c = Config().validate()
    assert (c.link_capacity_mbps, c.poll_interval_s, c.congested_threshold, c.detection_window) == (10.0, 1.0, 0.80, 3)
    assert (c.clear_threshold, c.clear_window, c.hold_down_s, c.alternate_max_util) == (0.50, 5, 10.0, 0.70)


@pytest.mark.parametrize("field,value", [
    ("poll_interval_s", 3.0), ("poll_interval_s", 0.2), ("clear_threshold", 0.9), ("detection_window", 0),
    ("alternate_max_util", 0), ("mode", "fast"), ("hold_down_s", -1), ("link_capacity_mbps", 0),
])
def test_invalid_config_rejected(field, value):
    c = Config()
    setattr(c, field, value)
    with pytest.raises(ValueError):
        c.validate()


def test_json_and_env_override(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"hold_down_s": 20, "congested_threshold": 0.9}))
    monkeypatch.setenv("SDN_CONFIG", str(p))
    monkeypatch.setenv("SDN_MODE", "static")
    c = Config.from_env()
    assert c.hold_down_s == 20 and c.congested_threshold == 0.9 and c.mode == "static"


def test_unknown_key_rejected(tmp_path, monkeypatch):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"nonsense": 1}))
    monkeypatch.setenv("SDN_CONFIG", str(p))
    with pytest.raises(ValueError):
        Config.from_env()
