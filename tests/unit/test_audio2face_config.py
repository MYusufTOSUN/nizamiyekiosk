"""Audio2FaceConfig + frame parser testleri (ağ açmaz)."""

from __future__ import annotations

import json

import pytest

from src.lipsync.audio2face import Audio2FaceConfig, Audio2FaceLipSync
from src.lipsync.mock import ARKIT_BLENDSHAPE_KEYS


def test_config_defaults() -> None:
    cfg = Audio2FaceConfig()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8011
    assert cfg.sample_rate == 24000


def test_config_dict_override() -> None:
    a2f = Audio2FaceLipSync({"host": "10.0.0.5", "port": 9000})
    assert a2f.config.host == "10.0.0.5"
    assert a2f.config.port == 9000


def test_parse_frame_text() -> None:
    msg = json.dumps(
        {
            "timestamp": 120_000,  # 120 ms in µs
            "blendshapes": {"jawOpen": 0.7, "mouthFunnel": 0.3},
        }
    )
    frame = Audio2FaceLipSync._parse_frame(msg)
    assert frame is not None
    assert frame.timestamp_ms == 120
    assert frame.values["jawOpen"] == pytest.approx(0.7)
    assert frame.values["mouthFunnel"] == pytest.approx(0.3)
    # eksik anahtarlar 0.0 olarak doldurulur
    assert frame.values["tongueOut"] == 0.0
    assert set(frame.values.keys()) == set(ARKIT_BLENDSHAPE_KEYS)


def test_parse_frame_bytes() -> None:
    msg = json.dumps({"timestamp": 0, "blendshapes": {"jawOpen": 0.1}}).encode("utf-8")
    frame = Audio2FaceLipSync._parse_frame(msg)
    assert frame is not None
    assert frame.values["jawOpen"] == pytest.approx(0.1)


def test_parse_frame_end_marker_returns_none() -> None:
    msg = json.dumps({"type": "end"})
    assert Audio2FaceLipSync._parse_frame(msg) is None


def test_parse_frame_alternate_values_key() -> None:
    msg = json.dumps({"timestamp": 0, "values": {"jawOpen": 0.5}})
    frame = Audio2FaceLipSync._parse_frame(msg)
    assert frame is not None
    assert frame.values["jawOpen"] == pytest.approx(0.5)
