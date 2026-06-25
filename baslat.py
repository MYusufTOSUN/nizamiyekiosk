"""BilimFest TEK-TIK BAŞLATICI — festival kiosk'u venv üzerinde doğru açar.

Bu dosya hangi python ile çalıştırılırsa çalıştırılsın (sistem python ya da
çift-tık) projenin `.venv` python'una geçer, ortam değişkenlerini ayarlar ve
`scripts/festival.py`'yi başlatır.

ÖNEMLİ: Bu yöntemde PowerShell **execution-policy bypass** ya da ayrı **venv
aktivasyonu GEREKMEZ** — venv'in python.exe'si doğrudan çağrılır (.exe çalıştırmak
PowerShell politikasına takılmaz ve venv python'unu kullanmak = venv "üzerinde").

Kullanım:
    python baslat.py                 # varsayılan mikrofon (aşağıdaki DEFAULT_DEVICE)
    python baslat.py --device 24     # belirli mikrofon
    python baslat.py --device "Hands-Free"   # isimle (Bluetooth index'i kaymasın diye)
    (ya da dosyaya çift tıkla)

Ses sistemi AÇILIŞTA OTOMATİK KALİBRE OLUR: boştayken ortam gürültüsünü ölçer,
tüm eşikleri (dinleme, barge) ona göre türetir; kısa bir El-Cezerî sesiyle eko'yu
ölçüp barge'ı ayarlar. Normalde HİÇBİR bayrak gerekmez.

Önce GÖRMEK/ölçmek istersen (önerilir):  python scripts/audio_check.py --device 1
  → ortam + eko ölçümünü ve barge testini canlı gösterir, önerilen değerleri basar.

İnce ayar bayrakları (verilirse ÖLÇÜLEN değeri EZER; verilmezse otomatik kalır):
    --silence 2.0            duraklama payı (çocuk yavaşsa artır)
    --min-voice 0.006        gürültü-kapısı RMS (fısıltı yutuluyorsa düşür)
    --barge-threshold 0.10   ziyaretçi barge eşiği (yine yanlış tetikliyorsa artır)
    --barge-echo-guard 350   El-Cezerî sustuktan sonra reverb beklemesi (ms)
    --barge-ms 450           gereken sürekli ziyaretçi konuşması (ms)
    --no-calibrate           otomatik kalibrasyonu atla (statik/override eşik kullan)
    --barge                  barge-in'i AÇ (şimdilik VARSAYILAN KAPALI; kod duruyor)
  Örn:  python baslat.py --device 1 --barge --barge-threshold 0.13

Operatör paneli (PIN) artık sunucuda doğrulanır: varsayılan '1206'. Değiştirmek
için sistemde  setx BFEST_OP_TOKEN "yeni-pin"  (bu launcher ortamı aynen aktarır).
Sergiden ÖNCE bir kez:  python scripts/prewarm_tts_cache.py   (TTS cache'i ısıtır).
"""
# Bu bir kullanıcı-launcher'ı: durum yazdırır (print) ve alt-süreç başlatır (subprocess).
# ruff: noqa: T201, S603
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Festivalde kullanılacak mikrofon. JBL Wave Beam 2 için index 24 ya da daha
# sağlam olarak isimle "Hands-Free". Cihaz değişirse burayı düzenle ya da
# komuta --device <N> geç.  (Bulmak için: scripts/mic_level_check.py)
DEFAULT_DEVICE = "1"

ROOT = Path(__file__).resolve().parent
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
FESTIVAL = ROOT / "scripts" / "festival.py"


def main() -> int:
    if not VENV_PY.exists():
        print(f"HATA: venv python bulunamadı:\n  {VENV_PY}")
        print("Kurulum eksik olabilir (.venv). SETUP.md / LAPTOP_SETUP.md'ye bak.")
        return 1
    if not FESTIVAL.exists():
        print(f"HATA: festival.py bulunamadı:\n  {FESTIVAL}")
        return 1

    # Ortam değişkenleri (festival profili + UTF-8 + XTTS lisans onayı).
    # ANTHROPIC_API_KEY festival.py tarafından kalıcı User ortamından otomatik yüklenir.
    env = dict(os.environ)
    env.setdefault("BFEST_CONFIG", "config.production.yaml")
    env["COQUI_TOS_AGREED"] = "1"
    env["PYTHONUTF8"] = "1"

    # Kullanıcı --device verdiyse ona dokunma; vermediyse varsayılanı ekle.
    args = sys.argv[1:]
    if not any(a == "--device" or a.startswith("--device=") for a in args):
        args = ["--device", DEFAULT_DEVICE, *args]

    cmd = [str(VENV_PY), str(FESTIVAL), *args]
    print("BilimFest başlatılıyor (venv üzerinde)…")
    print("  ", " ".join(cmd), "\n")
    try:
        return subprocess.call(cmd, env=env, cwd=str(ROOT))
    except KeyboardInterrupt:
        print("\n[baslat] kapatıldı.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
