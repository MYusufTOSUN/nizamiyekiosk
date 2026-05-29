"""Unreal bridge binary protocol roundtrip testleri."""

from __future__ import annotations

import pytest

from src.unreal_bridge.protocol import (
    ARKIT_BLENDSHAPE_KEYS,
    HEADER_SIZE,
    MAGIC,
    MSG_BLENDSHAPE,
    pack_blendshape_packet,
    unpack_blendshape_packet,
)


def test_constants() -> None:
    assert MAGIC == b"BFST"
    assert HEADER_SIZE == 8
    assert MSG_BLENDSHAPE == 0x01
    assert len(ARKIT_BLENDSHAPE_KEYS) == 52


def test_roundtrip_preserves_values() -> None:
    vals = {k: i / 100.0 for i, k in enumerate(ARKIT_BLENDSHAPE_KEYS)}
    packet = pack_blendshape_packet(
        timestamp_us=1_234_567,
        character_id="cezeri",
        values=vals,
    )
    ts, char, decoded = unpack_blendshape_packet(packet)
    assert ts == 1_234_567
    assert char == "cezeri"
    for k in ARKIT_BLENDSHAPE_KEYS:
        assert decoded[k] == pytest.approx(vals[k], abs=1e-6)


def test_missing_values_default_to_zero() -> None:
    packet = pack_blendshape_packet(
        timestamp_us=0,
        character_id="x",
        values={"jawOpen": 0.5},
    )
    _, _, decoded = unpack_blendshape_packet(packet)
    assert decoded["jawOpen"] == pytest.approx(0.5)
    assert decoded["tongueOut"] == 0.0


def test_long_character_id_truncated() -> None:
    long_id = "x" * 32
    packet = pack_blendshape_packet(timestamp_us=0, character_id=long_id, values={})
    _, char, _ = unpack_blendshape_packet(packet)
    assert char == "x" * 16  # 16 byte limit


def test_packet_size_is_fixed() -> None:
    packet = pack_blendshape_packet(timestamp_us=0, character_id="x", values={})
    # 8 byte header + 8 byte ts + 16 byte char + 1 byte count + 52*4 byte floats
    assert len(packet) == 8 + 8 + 16 + 1 + 52 * 4
    assert len(packet) == 241


def test_unpack_rejects_invalid_magic() -> None:
    packet = pack_blendshape_packet(timestamp_us=0, character_id="x", values={})
    bad = b"XXXX" + packet[4:]
    with pytest.raises(ValueError):
        unpack_blendshape_packet(bad)


def test_unpack_rejects_short_packet() -> None:
    with pytest.raises(ValueError):
        unpack_blendshape_packet(b"BFST" + b"\x00" * 4)
