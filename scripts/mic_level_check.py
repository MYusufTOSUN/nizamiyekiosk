"""Mikrofon teşhisi — aday cihazlardan kısa kayıt alıp seviye (RMS/peak) raporlar.

Boş transcript / "ses almıyor" durumunda hangi giriş cihazının gerçekten sinyal
aldığını bulmak için. Her cihaz için 3 sn kayıt alır; ekranda "KONUŞ" görünce
konuş. RMS ~0 → o cihaz sessiz (yanlış cihaz/BT profili). RMS > 0.01 → mic çalışıyor.

Kullanım:
    python scripts/mic_level_check.py                 # JBL + Realtek adaylarını dener
    python scripts/mic_level_check.py 1 15 24 17      # belirli cihaz index'lerini dener
"""
from __future__ import annotations

import sys

import numpy as np
import sounddevice as sd

SR = 16000
SECONDS = 3.0


def candidates() -> list[int]:
    if len(sys.argv) > 1:
        return [int(a) for a in sys.argv[1:]]
    devs = sd.query_devices()
    picks: list[int] = []
    for i, d in enumerate(devs):
        if d["max_input_channels"] <= 0:
            continue
        name = d["name"].lower()
        if "jbl" in name or "realtek" in name or "wave beam" in name:
            picks.append(i)
    return picks or [i for i, d in enumerate(devs) if d["max_input_channels"] > 0]


def main() -> int:
    devs = sd.query_devices()
    print(f"Varsayilan giris cihazi: {sd.default.device[0]}\n")
    best = (-1.0, None)
    for idx in candidates():
        try:
            name = devs[idx]["name"]
        except Exception:
            print(f"  [{idx}] gecersiz index, atlandi")
            continue
        print(f">>> [{idx}] {name}")
        print("    KONUS (3 sn) ...", flush=True)
        try:
            a = sd.rec(int(SECONDS * SR), samplerate=SR, channels=1, device=idx, dtype="float32")
            sd.wait()
        except Exception as e:  # noqa: BLE001
            print(f"    HATA: {e!r}\n")
            continue
        a = a.flatten()
        rms = float(np.sqrt(np.mean(a**2)))
        peak = float(np.max(np.abs(a)))
        verdict = "  <-- SINYAL VAR (mic bu)" if rms > 0.01 else "  (sessiz)"
        print(f"    RMS={rms:.5f}  peak={peak:.4f}{verdict}\n")
        if rms > best[0]:
            best = (rms, idx)
    if best[1] is not None:
        print(f"=> EN IYI: device {best[1]}  (RMS={best[0]:.5f})")
        print(f"   Canli calistir:  python scripts\\test_full_pipeline.py --device {best[1]} --seconds 6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
