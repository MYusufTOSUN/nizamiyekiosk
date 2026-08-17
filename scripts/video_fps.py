# -*- coding: utf-8 -*-
"""
Ara kare uretimi — 69 klip, 25 fps -> 50 fps.  video_post.py'den ONCE calisir.

  python scripts\\video_fps.py                     # kuru calisma
  python scripts\\video_fps.py --durum
  python scripts\\video_fps.py --uret              # RIFE ile (varsayilan)
  python scripts\\video_fps.py --uret --yontem mi  # minterpolate (RIFE kurulamazsa)
  python scripts\\video_fps.py --uret --klip meliksah_k1_s1_v1

NEDEN 50, 60 DEGIL
  RIFE --multi=2'de uretilen her kare iki gercek karenin TAM ORTASINA dusuyor;
  agin en dogru calistigi nokta orasi. 60 fps icin 0,2/0,4/0,6/0,8 konumlarinda
  kare uretmek gerekiyor - hem daha cok bozulma hem 2,4 kat hesap.
  Olculen kazanc farki yok: kare basina p90 adim 50 fps'te 1,13 aci-dakikasi,
  60 fps'te 0,94. Gozun ayirt sinirisi ~1,0. Ikisi de sinirin dibinde.
  Ekran 50 Hz'e alinir (50/50 = 1:1). TV Avrupa modeli, 50 Hz'i kesin kabul eder.

KABUL KONTROLU — bedava ve kesin
  --multi=2'de CIFT numarali cikti kareleri kaynak kareleriyle AYNI olmali.
  Betik bunu PSNR ile olcuyor: dusukse ara kodlama kalite yemis demektir.
  Ayrica siyah zemin seviyesi olculuyor - Pepper's Ghost'ta zemin yukselirse
  camda asili parlak bir dikdortgen olarak gorunur.

CIKTI  video/02b_50fps/<kod>__50.mp4     (ham klipler ASLA degistirilmez)
"""
import argparse, json, shutil, subprocess, sys, time
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
HAM = KOK / "video" / "02_ham"
CIK = KOK / "video" / "02b_50fps"
LOG = KOK / "video" / "_log" / "fps_manifest.jsonl"
HEDEF_FPS = 50

# Practical-RIFE deposunun yeri. Baska yerdeyse RIFE_KOK ortam degiskeniyle ver.
import os
RIFE = Path(os.environ.get("RIFE_KOK", KOK.parent / "Practical-RIFE"))


def ffprobe(p: Path, alan: str) -> str:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", f"stream={alan}", "-of", "csv=p=0:nk=1", str(p)],
                       capture_output=True, text=True)
    return r.stdout.strip()


def siyah_seviye(p: Path, sn: float = 3.0) -> float:
    """Karenin en koyu %50'sinin ortalamasi. Pepper's Ghost icin 0'a yakin olmali."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(sn), "-i", str(p), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True)
    if not r.stdout:
        return -1.0
    import statistics
    b = sorted(r.stdout)
    return statistics.fmean(b[: len(b) // 2])


def cift_kare_psnr(kaynak: Path, uretilen: Path) -> float:
    """--multi=2'de cikti kareleri 0,2,4... kaynagin 0,1,2...'si olmali.
    select ile ciftleri ayikla, PSNR olc. 45 dB alti = ara kodlama kalite yedi."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-t", "6", "-i", str(uretilen), "-t", "6", "-i", str(kaynak),
         "-filter_complex",
         "[0:v]select='not(mod(n,2))',setpts=N/25/TB[a];[a][1:v]psnr=stats_file=-",
         "-f", "null", "-"], capture_output=True, text=True)
    for parca in reversed(r.stderr.split()):
        if parca.startswith("average:"):
            try:
                return float(parca.split(":")[1])
            except ValueError:
                pass
    import re
    m = re.findall(r"psnr_avg:([0-9.]+)", r.stdout)
    return float(m[-1]) if m else -1.0


def komut_rife(kaynak: Path, hedef: Path) -> list:
    return [sys.executable, str(RIFE / "inference_video.py"),
            "--multi=2", f"--video={kaynak}", f"--output={hedef}"]


def komut_mi(kaynak: Path, hedef: Path) -> list:
    # Yedek yol. RIFE yoksa calisir; kalitesi dusuk ama is gorur.
    return ["ffmpeg", "-v", "error", "-stats", "-y", "-i", str(kaynak),
            "-vf", f"minterpolate=fps={HEDEF_FPS}:mi_mode=mci:mc_mode=aobmc:"
                   "me_mode=bidir:vsbmc=1",
            "-c:v", "libx264", "-crf", "12", "-preset", "medium",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709",
            "-c:a", "copy", "-movflags", "+faststart", str(hedef)]


def yapildi() -> dict:
    if not LOG.exists():
        return {}
    d = {}
    for s in LOG.read_text(encoding="utf-8").splitlines():
        try:
            k = json.loads(s)
            d[k["kod"]] = k
        except Exception:
            pass
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uret", action="store_true")
    ap.add_argument("--durum", action="store_true")
    ap.add_argument("--klip")
    ap.add_argument("--zorla", action="store_true")
    ap.add_argument("--yontem", choices=["rife", "mi"], default="rife")
    ap.add_argument("--partikul", action="store_true",
                    help="sadece partikul dongulerini hedef fps'e cevir (bir kez)")
    a = ap.parse_args()

    if a.partikul:
        VAR = KOK / "video" / "_post_varlik" / "partikul"
        for ad in ("arka", "on"):
            kay, hed = VAR / f"{ad}.mp4", VAR / f"{ad}_{HEDEF_FPS}.mp4"
            if not kay.exists():
                sys.exit(f"HATA: {kay} yok.")
            print(f"{kay.name} -> {hed.name}")
            if not a.uret:
                continue
            # Dongu 30 sn; ses yok. minterpolate yeter - partikuller sonuk ve
            # yavas, RIFE kurulumunu bunun icin beklemeye gerek yok.
            subprocess.run(["ffmpeg", "-v", "error", "-stats", "-y", "-i", str(kay),
                            "-vf", f"minterpolate=fps={HEDEF_FPS}:mi_mode=mci:"
                                   "mc_mode=aobmc:me_mode=bidir:vsbmc=1",
                            "-c:v", "libx264", "-crf", "12", "-preset", "medium",
                            "-pix_fmt", "yuv420p", "-an", str(hed)], check=True)
        print("KURU CALISMA — uretmek icin --uret" if not a.uret else "bitti")
        return

    # --- HAM KLIPLERI KORUMA ---
    # 02_ham, $289,52'lik uretimin TEK gercek kopyasi; kiosk/videolar ona sabit bag.
    # Bu betik oraya asla yazmaz, yine de yolu dogrular.
    if CIK.resolve() == HAM.resolve() or HAM.resolve() in CIK.resolve().parents:
        sys.exit("HATA: cikti yolu ham kliplerin icine denk geliyor. DURDURULDU.")
    kaynaklar = sorted(HAM.glob("*__ham.mp4"))
    if len(kaynaklar) < 69:
        print(f"!! UYARI: video/02_ham icinde {len(kaynaklar)} klip var, 69 bekleniyordu.")
    print(f"ham klipler: {len(kaynaklar)} adet, SALT OKUNUR ({HAM})")
    print(f"cikti      : {CIK}")

    if a.klip:
        kaynaklar = [p for p in kaynaklar if p.name.startswith(a.klip + "__")]
    bitmis = yapildi()

    if a.durum:
        kalan = [p for p in kaynaklar if p.name.split("__")[0] not in bitmis]
        print(f"kaynak {len(kaynaklar)} · islenmis {len(bitmis)} · kalan {len(kalan)}")
        kotu = [k for k, v in bitmis.items() if v.get("psnr", 99) < 45]
        if kotu:
            print(f"!! PSNR dusuk ({len(kotu)}): {', '.join(kotu[:6])}")
        return

    if a.yontem == "rife" and not (RIFE / "inference_video.py").exists():
        sys.exit(f"HATA: Practical-RIFE bulunamadi -> {RIFE}\n"
                 f"  RIFE_KOK ortam degiskeniyle yolu ver, ya da --yontem mi kullan.\n"
                 f"  Kurulum: docs/VIDEO_PIPELINE_5090.md")
    if not shutil.which("ffmpeg"):
        sys.exit("HATA: ffmpeg PATH'te yok.")

    kalan = [p for p in kaynaklar if a.zorla or p.name.split("__")[0] not in bitmis]
    print(f"yontem   : {a.yontem}   hedef {HEDEF_FPS} fps")
    print(f"islenecek: {len(kalan)} / {len(kaynaklar)}")
    if not a.uret:
        print("\nKURU CALISMA — hicbir sey uretilmedi. Uretmek icin: --uret")
        if kalan:
            k = kalan[0]
            c = (komut_rife if a.yontem == "rife" else komut_mi)(
                k, CIK / k.name.replace("__ham", "__50"))
            print("\nornek komut:\n  " + " ".join(c))
        return

    CIK.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    for i, kaynak in enumerate(kalan, 1):
        kod = kaynak.name.split("__")[0]
        hedef = CIK / f"{kod}__50.mp4"
        print(f"\n[{i}/{len(kalan)}] {kod}")
        c = (komut_rife if a.yontem == "rife" else komut_mi)(kaynak, hedef)
        r = subprocess.run(c, cwd=str(RIFE) if a.yontem == "rife" else None)
        if r.returncode != 0 or not hedef.exists():
            sys.exit(f"HATA: {kod} islenemedi (donus {r.returncode}). Parti durduruldu.")

        # --- kabul kontrolleri ---
        fps = ffprobe(hedef, "avg_frame_rate")
        sy_kaynak, sy_hedef = siyah_seviye(kaynak), siyah_seviye(hedef)
        psnr = cift_kare_psnr(kaynak, hedef)
        uyari = []
        if fps.split("/")[0] != str(HEDEF_FPS * int(fps.split("/")[1] or 1)):
            uyari.append(f"fps {fps}")
        if sy_hedef > sy_kaynak + 1.5:
            uyari.append(f"SIYAH YUKSELDI {sy_kaynak:.2f} -> {sy_hedef:.2f}")
        if 0 < psnr < 45:
            uyari.append(f"PSNR dusuk {psnr:.1f} dB")
        print(f"   fps {fps} · siyah {sy_kaynak:.2f}->{sy_hedef:.2f} · "
              f"cift kare PSNR {psnr:.1f} dB" + ("   !! " + " · ".join(uyari) if uyari else "   OK"))

        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kod": kod, "cikti": hedef.name, "yontem": a.yontem,
                                "boyut": hedef.stat().st_size, "fps": fps,
                                "siyah_kaynak": round(sy_kaynak, 2),
                                "siyah_hedef": round(sy_hedef, 2),
                                "psnr": round(psnr, 1), "uyari": uyari,
                                "zaman": time.strftime("%Y-%m-%dT%H:%M:%S")},
                               ensure_ascii=False) + "\n")

    print(f"\nBITTI · {len(kalan)} klip · {(time.time()-t0)/60:.1f} dakika")
    print("SIRADAKI: python scripts\\video_post.py --uret --kaynak 02b_50fps")


if __name__ == "__main__":
    main()
