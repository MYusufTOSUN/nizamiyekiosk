"""TTS disk-cache ortak yardımcıları — sağlayıcı izolasyonu + mtime prune.

Üç TTS sağlayıcısı (XTTS / Edge / ElevenLabs) aynı kök cache klasörünü
(``data/tts_cache``) paylaşır AMA her biri KENDİ alt-klasörüne yazar
(``.../xtts``, ``.../edge``, ``.../eleven``). Böylece:
- bir sağlayıcının prune'u DİĞERİNİN dosyalarını silmez (eski hata: XTTS glob'u
  hepsini siliyordu),
- her sağlayıcı kendi ``cache_max_entries`` bütçesini bağımsız yönetir.

28 saatlik sergide sınırsız disk büyümesini (uzun-kuyruk LLM cevapları) önler.
"""
from __future__ import annotations

from pathlib import Path

from src.core.logger import get_logger

_log = get_logger(component="tts.cache")


def provider_cache_dir(root: str, provider: str) -> Path:
    """``<root>/<provider>`` alt-klasörünü döndür (yoksa oluştur, hata olursa root)."""
    base = Path(root) / provider
    try:
        base.mkdir(parents=True, exist_ok=True)
        return base
    except OSError:
        return Path(root)


def prune_cache(cache_dir: Path, max_entries: int, pattern: str = "*.pcm") -> None:
    """En eski (mtime) dosyaları sil; ``max_entries`` üstündekileri buda.

    Yalnız ``cache_dir`` içindeki ``pattern`` dosyalarına dokunur (alt-klasör
    izolasyonu sayesinde başka sağlayıcının dosyası bu glob'a girmez).
    """
    if max_entries <= 0 or not cache_dir.exists():
        return
    try:
        files = sorted(cache_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    excess = len(files) - max_entries
    for f in files[: max(0, excess)]:
        try:
            f.unlink()
        except OSError:
            pass


__all__ = ["provider_cache_dir", "prune_cache"]
