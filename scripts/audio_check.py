"""BilimFest SES TEŞHİS + KALİBRASYON aracı.

Festival PC'sinde mikrofon (device 1) + hoparlörü GERÇEK ortamda ölçer, eşikleri
türetir ve dinleme + barge'ı CANLI test eder. Kiosk'u başlatmadan önce çalıştır:
"şu an sistem ne ölçüyor, barge sağlam mı" sorusunu gözle yanıtlar.

Akış:
  1) Cihaz bilgisi
  2) Canlı RMS metre (ortam vs sesin)
  3) Ortam kalibrasyonu (sessizken taban ölçümü -> eşik türetimi)
  4) Eko kalibrasyonu (test sesi çalıp mikrofona sızan eko + reverb)
  5) Dinleme testi (3 kez konuş -> yakalandı mı, seviye ne)
  6) Barge testi: (a) SESSİZ tur — yanlış-tetik OLMAMALI
                  (b) KONUŞ tur — gerçek kesme OLMALI
  7) Önerilen baslat.py bayrakları

Kullanım:  python scripts/audio_check.py [--device 1] [--ambient 3]
"""
# Etkileşimli teşhis: print + input + while-sleep serbest.
# ruff: noqa: T201
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

import sounddevice as sd

from src.audio import engine as E


def _force_utf8() -> None:
    """Türkçe çıktı düzgün görünsün diye (cp1254 yerine) UTF-8 modunda yeniden
    başlat — baslat.py'nin kiosk'a yaptığının aynısı; burada kendimize yaparız."""
    if os.name == "nt" and not os.environ.get("PYTHONUTF8"):
        os.environ["PYTHONUTF8"] = "1"
        os.execv(sys.executable, [sys.executable, *sys.argv])  # noqa: S606


def _bar(level: float, scale: float = 0.3, width: int = 40) -> str:
    n = min(width, int(level / scale * width))
    return "#" * n + "·" * (width - n)


def meter(mic: E.MicStream, seconds: float, label: str) -> None:
    print(f"\n{label} ({seconds:.0f} sn) — canlı seviye:")
    t0 = time.monotonic()
    peak = 0.0
    while time.monotonic() - t0 < seconds:
        f = mic.read_frame(0.1)
        if f is None:
            continue
        r = E.rms(f)
        peak = max(peak, r)
        print(f"  RMS {r:.4f} |{_bar(r)}|", end="\r", flush=True)
    print(f"\n  -> tepe: {peak:.4f}", flush=True)


def listen_test(mic: E.MicStream, cal: E.Calibration, n: int = 3) -> None:
    cfg = E.EndpointConfig()
    ep = E.Endpointer(cal, cfg)
    print("\n--- DİNLEME TESTİ --- (her seferinde kısa bir cümle söyle)")
    for i in range(1, n + 1):
        print(f"\n[{i}/{n}] Konuş...")
        t0 = time.monotonic()
        audio, reason = ep.listen(mic, lambda: True)
        dur = time.monotonic() - t0
        if audio is None:
            print(f"  [X] yakalanmadı ({reason}) — {dur:.1f}s")
        else:
            sec = audio.size / E.TARGET_SR
            print(f"  [OK] yakalandı: {sec:.1f}s ses, durum={reason} (rms={E.rms(audio):.3f})")


def barge_test(cal: E.Calibration, mic: E.MicStream) -> tuple[bool, float | None]:
    """Test sesini (sd.play, non-blocking) çal + referans-kapılı barge'ı izle."""
    spk = E.SpeakerStream()
    tone = E.make_test_tone(seconds=5.0, sr=spk.sr)
    dur = tone.size / spk.sr
    stop_evt, barge_evt = threading.Event(), threading.Event()
    det = E.BargeDetector(cal)
    mon = threading.Thread(target=det.monitor, args=(mic, spk, stop_evt, barge_evt), daemon=True)
    mic.drain()
    mon.start()
    spk.play_array(tone)  # NON-BLOCKING; arka planda çalar
    t0 = time.monotonic()
    while time.monotonic() - t0 < dur:
        if barge_evt.is_set():
            break
        time.sleep(0.05)
    when = (time.monotonic() - t0) if barge_evt.is_set() else None
    stop_evt.set()
    spk.stop()
    mon.join(timeout=1.0)
    spk.close()
    return barge_evt.is_set(), when


def main() -> int:
    ap = argparse.ArgumentParser(description="BilimFest ses teşhis + kalibrasyon")
    ap.add_argument("--device", default="1", help="mikrofon cihaz index'i (varsayılan 1)")
    ap.add_argument("--ambient", type=float, default=3.0, help="ortam ölçüm süresi (sn)")
    args = ap.parse_args()
    dev: object = int(args.device) if str(args.device).isdigit() else args.device

    print("=" * 60)
    print("BİLİMFEST SES TEŞHİS + KALİBRASYON")
    print("=" * 60)
    info = sd.query_devices(dev)
    print(f"Mikrofon  : [{dev}] {info['name']}  native={int(info['default_samplerate'])}Hz "
          f"kanal={info['max_input_channels']}")
    out = sd.query_devices(kind="output")
    print(f"Hoparlör  : {out['name']}  native={int(out['default_samplerate'])}Hz")

    mic = E.MicStream(dev)
    try:
        mic.start()
    except Exception as exc:  # noqa: BLE001
        print(f"\nHATA: mikrofon açılamadı: {exc}")
        return 1

    try:
        # 2) canlı metre
        meter(mic, 2.0, "Önce sessiz dur (ortam)")
        input("\n[Enter] -> ortam kalibrasyonu başlasın (SESSİZ ol)...")

        # 3+4) tam kalibrasyon (ortam + eko)
        print(f"\nOrtam ölçülüyor ({args.ambient:.0f}s, sessiz ol)...", flush=True)
        spk = E.SpeakerStream()
        tone = E.make_test_tone(seconds=1.6, sr=spk.sr)
        print("Eko ölçülüyor: kısa bir test sesi çalınacak — KONUŞMA...", flush=True)
        cal = E.calibrate(mic, ambient_seconds=args.ambient, speaker=spk, test_pcm=tone)
        spk.close()

        print("\n" + "-" * 60)
        print("KALİBRASYON SONUCU")
        print("-" * 60)
        print(cal.summary())
        for note in cal.notes:
            print(f"  - {note}")

        # 5) dinleme testi
        input("\n[Enter] -> dinleme testi...")
        listen_test(mic, cal)

        # 6) barge testi — önce SESSİZ (yanlış-tetik), sonra KONUŞ (gerçek)
        input("\n[Enter] -> BARGE testi 1/2: test sesi çalacak, SEN SUS (kesilmemeli)...")
        fired_silent, _ = barge_test(cal, mic)
        print("  -> SESSİZ turda barge:", "TETİKLENDİ [X] (yanlış-tetik!)" if fired_silent
              else "tetiklenmedi [OK]")

        input("\n[Enter] -> BARGE testi 2/2: test sesi çalarken ÜSTÜNE KONUŞ (kesilmeli)...")
        fired_talk, when = barge_test(cal, mic)
        print("  -> KONUŞ turda barge:", f"TETİKLENDİ [OK] ({when:.1f}s'de)" if fired_talk
              else "tetiklenmedi [X] (eşik yüksek olabilir)")

        # 7) öneri
        print("\n" + "=" * 60)
        print("ÖNERİLEN baslat.py BAYRAKLARI")
        print("=" * 60)
        print(f"  python baslat.py --device {dev} \\")
        print(f"      --barge-threshold {cal.barge_threshold:.3f} \\")
        print(f"      --barge-ref-quiet {cal.barge_ref_quiet:.3f} \\")
        print(f"      --barge-echo-guard {cal.echo_guard_ms} \\")
        print(f"      --min-voice {cal.min_voice_rms:.3f} --threshold {cal.listen_threshold:.3f}")
        if fired_silent:
            print("\n  ! SESSİZ turda tetiklendi -> --barge-threshold'u ARTIR "
                  "veya --barge-echo-guard'ı yükselt.")
        if not fired_talk:
            print("\n  ! KONUŞ turda tetiklenmedi -> --barge-threshold'u DÜŞÜR.")
        if not cal.snr_ok:
            print("\n  ! Ortam gürültüsü yüksek — Windows'ta mik seviyesini düşür/mikrofonu "
                  "yaklaştır; gerekirse yönlü/yaka mikrofonu kullan.")
        print("\nNot: Kiosk açılışta bu kalibrasyonu OTOMATİK yapar; CLI ile EZEBİLİRSİN.")
    finally:
        mic.stop()
    return 0


if __name__ == "__main__":
    _force_utf8()
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n[audio_check] iptal.")
