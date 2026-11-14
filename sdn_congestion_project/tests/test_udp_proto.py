"""TC-03 (sender format / validation) and TC-19 (invalid input rejected)."""
import pytest
from common import udp_proto as P


def test_pack_unpack_roundtrip():
    data = P.pack_packet(7, 12345, 1700000000.25, 1000)
    assert len(data) == 1000
    assert P.unpack_header(data) == (7, 12345, 1700000000.25)


def test_minimum_size_enforced():
    with pytest.raises(P.ValidationError):
        P.pack_packet(1, 0, 0.0, 13)
    assert len(P.pack_packet(1, 0, 0.0, P.HEADER_SIZE)) == 14


def test_short_datagram_ignored():
    assert P.unpack_header(b"abc") is None


@pytest.mark.parametrize("fn,bad", [
    (P.validate_ip, "10.0.0.300"), (P.validate_ip, "abc"),
    (P.validate_port, 0), (P.validate_port, 70000), (P.validate_port, "x"),
    (P.validate_rate, 0), (P.validate_rate, -1), (P.validate_rate, "fast"),
    (P.validate_size, 5), (P.validate_size, 2000),
    (P.validate_duration, 0), (P.validate_duration, -3),
])
def test_invalid_inputs_rejected(fn, bad):
    with pytest.raises(P.ValidationError):
        fn(bad)


def test_valid_inputs_accepted():
    assert P.validate_ip("10.0.0.3") == "10.0.0.3"
    assert P.validate_port("5001") == 5001
    assert P.validate_rate("2.5") == 2.5
    assert P.validate_size(1000) == 1000
