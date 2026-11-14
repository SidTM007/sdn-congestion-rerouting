"""UDP payload format and input validation shared by sender, receiver and tests.

Payload layout (network byte order), SRS Section 3.4:
    flow_id (2 B) | sequence_number (4 B) | send_timestamp (8 B, double) | padding
"""
import ipaddress
import struct

HEADER = struct.Struct("!HId")
HEADER_SIZE = HEADER.size          # 14 bytes = minimum payload size
MAX_PAYLOAD = 1472                 # fits in a 1500-byte MTU (1472 + 8 UDP + 20 IP)


class ValidationError(ValueError):
    """Raised for invalid command-line / configuration input (FR: reject invalid input clearly)."""


def pack_packet(flow_id, seq, timestamp, size):
    if size < HEADER_SIZE:
        raise ValidationError("packet size must be at least %d bytes" % HEADER_SIZE)
    return HEADER.pack(flow_id, seq & 0xFFFFFFFF, timestamp) + b"\x00" * (size - HEADER_SIZE)


def unpack_header(data):
    """Return (flow_id, seq, timestamp) or None if the datagram is too short."""
    if len(data) < HEADER_SIZE:
        return None
    return HEADER.unpack_from(data)


def validate_ip(value):
    try:
        return str(ipaddress.IPv4Address(value))
    except Exception:
        raise ValidationError("invalid IPv4 address: %r" % (value,))


def validate_port(value):
    try:
        port = int(value)
    except Exception:
        raise ValidationError("invalid UDP port: %r" % (value,))
    if not 1 <= port <= 65535:
        raise ValidationError("UDP port must be 1-65535, got %d" % port)
    return port


def validate_rate(value):
    try:
        rate = float(value)
    except Exception:
        raise ValidationError("invalid rate: %r" % (value,))
    if not rate > 0:
        raise ValidationError("rate must be greater than 0 Mbps, got %s" % value)
    return rate


def validate_size(value):
    try:
        size = int(value)
    except Exception:
        raise ValidationError("invalid packet size: %r" % (value,))
    if not HEADER_SIZE <= size <= MAX_PAYLOAD:
        raise ValidationError("packet size must be %d-%d bytes, got %d" % (HEADER_SIZE, MAX_PAYLOAD, size))
    return size


def validate_duration(value):
    try:
        d = float(value)
    except Exception:
        raise ValidationError("invalid duration: %r" % (value,))
    if not d > 0:
        raise ValidationError("duration must be greater than 0 seconds, got %s" % value)
    return d
