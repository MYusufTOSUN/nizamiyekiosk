"""Whisper modelini ön yükle.

Festival arifesinde internet kesintisi olmasın diye modeli `data/models/whisper`
altına çekiyoruz. faster-whisper otomatik indirebilir ama biz konumu sabitliyoruz.

Kullanım:
    python scripts/download_models.py
    python scripts/download_models.py --model large-v3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def download_whisper(model: str, output_dir: Path) -> None:
    try:
        from faster_whisper import download_model  # type: ignore[import-not-found]
    except ImportError:
        print("HATA: faster-whisper kurulu değil. `pip install -e .[stt]`")
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"İndiriliyor: {model} → {output_dir}")
    download_model(model, output_dir=str(output_dir), local_files_only=False)
    print(f"OK Whisper {model} indirildi.")


def main() -> int:
    parser = argparse.ArgumentParser(description="BilimFest model indirici")
    parser.add_argument(
        "--model",
        default="large-v3",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="faster-whisper model adı",
    )
    parser.add_argument(
        "--output-dir",
        default="data/models/whisper",
        help="Hedef klasör",
    )
    args = parser.parse_args()
    download_whisper(args.model, Path(args.output_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
