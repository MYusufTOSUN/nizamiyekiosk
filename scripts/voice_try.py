"""XTTS dahili seslerini listele + aday erkek seslerin örnek WAV'larını üret.

El-Cezerî için tok/heybetli bir ses seçmek üzere: data/voice_samples/<isim>.wav
dosyalarını dinle, beğendiğini config'te builtin_speaker yap.

Kullanım: python scripts/voice_try.py
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import soundfile as sf

from src.core.config import get_config
from src.tts.xtts_local import XTTS_NATIVE_SR, XTTSConfig, XTTSLocalTTS

SAMPLE = (
    "Ben El-Cezerî'yim evladım. Asırlar önce yaşamış bir mucitim. "
    "Su saatleri, fil saati, tavus kuşu otomatları yaptım. Hangi makinemi merak ediyorsun?"
)
# Derin/olgun erkek aday sesler (modelde olanlar üretilir)
CANDIDATES = [
    "Damien Black", "Aaron Dreschner", "Royston Min", "Viktor Menelaos",
    "Baldur Sanjin", "Damjan Chapman", "Wulf Carlevaro", "Ludvig Milivoj",
    "Torcull Diarmuid", "Eugenio Mataracı", "Kazuhiko Atallah", "Craig Gutsy",
    "Viktor Eka", "Abrahan Mack", "Gilberto Mathias", "Zacharie Aimilios",
]


def _speaker_names(tts: XTTSLocalTTS) -> list[str]:
    m = tts._model
    for path in (
        lambda: list(m.model.speaker_manager.speakers.keys()),
        lambda: list(m.synthesizer.tts_model.speaker_manager.speakers.keys()),
        lambda: list(m.speaker_manager.speakers.keys()),
    ):
        try:
            names = path()
            if names:
                return names
        except Exception:  # noqa: BLE001, S112
            continue
    return []


async def main() -> int:
    cfg = get_config()
    conf = {**cfg.tts.config, "use_voice_clone": False}
    tts = XTTSLocalTTS(XTTSConfig(**conf))
    await tts._ensure_model()

    names = _speaker_names(tts)
    print(f"\n=== Modelde {len(names)} dahili ses var ===")
    print(", ".join(names) if names else "(liste alınamadı)")

    out = Path("data/voice_samples")
    out.mkdir(parents=True, exist_ok=True)
    todo = [c for c in CANDIDATES if not names or c in names]
    print(f"\n=== {len(todo)} aday için örnek üretiliyor → {out}/ ===")
    for name in todo:
        tts.config.builtin_speaker = name
        tts._speaker_cache.clear()
        pcm = bytearray()
        try:
            async for chunk in tts.synthesize_stream(SAMPLE, "cezeri"):
                pcm.extend(chunk)
        except Exception as e:  # noqa: BLE001
            print(f"  [atlandı] {name}: {e}")
            continue
        audio = np.frombuffer(bytes(pcm), dtype=np.int16)
        fp = out / f"{name.replace(' ', '_')}.wav"
        sf.write(str(fp), audio, XTTS_NATIVE_SR)
        print(f"  ✓ {name:20} -> {fp.name}  ({audio.size / XTTS_NATIVE_SR:.1f} sn)")

    print("\nDinle: data/voice_samples/*.wav — beğendiğini söyle, config'e yazayım.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
