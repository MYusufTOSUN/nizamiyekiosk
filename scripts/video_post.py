# -*- coding: utf-8 -*-
"""
Hologram video post-produksiyonu — 69 klip.

  python scripts\\video_post.py            # kuru calisma, hicbir sey uretmez
  python scripts\\video_post.py --uret     # gercekten uretir
  python scripts\\video_post.py --uret --klip gazali_int_1
  python scripts\\video_post.py --durum    # ne islenmis, ne kalmis

ZINCIR (tek klipte olculerek dogrulandi, bkz. docs/KABIN_DERINLIK_RAPORU.md §8)
  alt gradyan -> arka partikul -> on partikul -> karakter bazli atmosfer
  -> on pus -> scale 2528x1896 (lanczos) -> pad 3840x2160:656:132 -> x264 crf 14

NEDEN BOYLE
  * Ara islem 10-bit: 8-bit'te alt gradyan bantlaniyor.
  * Siyah KIRPILMIYOR. Kaynagin arka plani zaten tam Y=16 / U=V=128; sadece dogru
    aralik donusumu siyahi %100'e getiriyor. Esik koymak yuzden 6-10 ton yiyor.
  * Vinyet YOK. Kenarlar zaten temiz; vinyet Meliksah'in tuglarini (satir 28) ve
    Nizamulmulk'un kollarini (sutun 209) kirpiyordu.
  * Ses HIC DOKUNULMADAN kopyalaniyor (-c:a copy).
  * format=gray KULLANMA: blend'in format pazarligi griyi gorup TUM zinciri griye
    cekiyor (doygunluk 122,6 -> 2,0 olculdu). Atmosfer sonuk oldugu icin maskesiz
    lighten ayni isi goruyor.

CIKTI  video/03_post/<kod>__post.mp4
       Kioska gecirmek icin ayrica: scripts/kiosk_build.py + hardlink tazeleme.
"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
HAM = KOK / "video" / "02_ham"
CIK = KOK / "video" / "03_post"
VAR = KOK / "video" / "_post_varlik"
LOG = KOK / "video" / "_log" / "post_manifest.jsonl"

# ---- filtre grafigi -------------------------------------------------------
# [0] kaynak · [1] arka partikul · [2] on partikul · [3] alt gradyan maskesi
# [4] atmosfer arka · [5] atmosfer on pus · [6] partikul kazanc maskesi
GRAF = (
    "[6:v]scale=1440:1080:flags=bilinear,format=gbrp10le,setsar=1,split[g1][g2];"
    "[0:v]scale=in_range=limited:out_range=full,format=gbrp10le,setsar=1[fig];"
    "[1:v]format=gbrp10le,setsar=1[pb0];"
    "[2:v]format=gbrp10le,gblur=sigma=2.0,setsar=1[pf0];"
    "[pb0][g1]blend=all_mode=multiply:shortest=1,format=gbrp10le[pb];"
    "[pf0][g2]blend=all_mode=multiply:shortest=1,format=gbrp10le[pf];"
    "[3:v]format=gbrp10le,setsar=1[gal];"
    "[fig][pb]blend=all_mode=lighten:shortest=1,format=gbrp10le[s1];"
    "[s1][pf]blend=all_mode=lighten:shortest=1,format=gbrp10le[s2];"
    "[s2][gal]blend=all_mode=multiply:shortest=1,format=gbrp10le[s3];"
    "[s3]scale=2528:1896:flags=lanczos,pad=3840:2160:656:132:black,format=gbrp10le[k1];"
    "[4:v]format=gbrp10le,setsar=1[bg];"
    "[k1][bg]blend=all_mode=lighten:shortest=1,format=gbrp10le[s4];"
    "[5:v]format=gbrp10le,setsar=1[fg];"
    "[s4][fg]blend=all_mode=screen:shortest=1,format=gbrp10le[s5];"
    "[s5]scale=in_range=full:out_range=limited,format=yuv420p[v]"
)


def karakter(kod: str) -> str:
    for k in ("gazali", "nizamulmulk", "meliksah"):
        if kod.startswith(k):
            return k
    raise ValueError(f"karakter cozulemedi: {kod}")


def komut(kaynak: Path, hedef: Path, kar: str, pson: str = "") -> list:
    # pson: partikul dosya soneki. Kaynak 50 fps ise "_50" gelir; 25 fps'lik
    # dongu 50 fps ciktida kare tekrarina duser ve FIGUR AKARKEN PARTIKUL
    # BASAMAKLANIR. Bu yuzden dongulerin de ayni fps'te olmasi sart.
    return [
        "ffmpeg", "-v", "error", "-stats", "-y",
        "-i", str(kaynak),
        "-stream_loop", "-1", "-i", str(VAR / "partikul" / f"arka{pson}.mp4"),
        "-stream_loop", "-1", "-i", str(VAR / "partikul" / f"on{pson}.mp4"),
        "-loop", "1", "-i", str(VAR / "mask_alt16.png"),
        "-loop", "1", "-i", str(VAR / f"atmf_{kar}_arka.png"),
        "-loop", "1", "-i", str(VAR / f"atmf_{kar}_on.png"),
        "-loop", "1", "-i", str(VAR / f"atmf_{kar}_gain.png"),
        "-filter_complex", GRAF,
        "-map", "[v]", "-map", "0:a",
        "-c:v", "libx264", "-crf", "14", "-preset", "slow",
        "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "5.1",
        "-x264-params", "aq-mode=3",
        "-color_range", "tv", "-colorspace", "bt709",
        "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "copy", "-movflags", "+faststart",
        str(hedef),
    ]


def yapildi() -> dict:
    if not LOG.exists():
        return {}
    d = {}
    for satir in LOG.read_text(encoding="utf-8").splitlines():
        try:
            k = json.loads(satir)
            d[k["kod"]] = k
        except Exception:
            pass
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uret", action="store_true", help="gercekten uret")
    ap.add_argument("--durum", action="store_true", help="ozet goster")
    ap.add_argument("--klip", help="tek klip kodu")
    ap.add_argument("--zorla", action="store_true", help="yapilmis olsa da yeniden uret")
    ap.add_argument("--kaynak", default="02b_50fps",
                    help="video/ altinda kaynak dizin (varsayilan 02b_50fps; "
                         "ara kare uretimi atlanacaksa 02_ham)")
    a = ap.parse_args()

    KAY = KOK / "video" / a.kaynak
    if not KAY.is_dir():
        sys.exit(f"HATA: kaynak dizin yok -> {KAY}\n"
                 f"  Once ara kareleri uret: python scripts\\video_fps.py --uret\n"
                 f"  Ya da ara kare olmadan: --kaynak 02_ham")
    desen = "*__50.mp4" if a.kaynak != "02_ham" else "*__ham.mp4"

    # --- HAM KLIPLERI KORUMA ---
    # 02_ham 69 klibin TEK gercek kopyasi ($289,52'lik uretimin ciktisi) ve
    # kiosk/videolar ona SABIT BAG. Bu betik oraya asla yazmaz; yine de
    # calismadan once kaniti sunar ve cikti yolunu dogrular.
    if CIK.resolve() == HAM.resolve() or HAM.resolve() in CIK.resolve().parents:
        sys.exit("HATA: cikti yolu ham kliplerin icine denk geliyor. DURDURULDU.")
    ham_sayi = len(list(HAM.glob("*__ham.mp4")))
    if ham_sayi < 69:
        print(f"!! UYARI: video/02_ham icinde {ham_sayi} klip var, 69 bekleniyordu.")
    print(f"ham klipler: {ham_sayi} adet, SALT OKUNUR ({HAM})")
    print(f"kaynak     : {KAY}")
    print(f"cikti      : {CIK}  (ayri dizin, ham dosyalara dokunulmaz)")

    kaynaklar = sorted(KAY.glob(desen))
    if not kaynaklar:
        sys.exit(f"HATA: {KAY} icinde {desen} bulunamadi.")

    # Kaynagin fps'i partikul dongulerinin fps'ini belirler.
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0:nk=1",
                        str(kaynaklar[0])], capture_output=True, text=True)
    pay, _, bol = r.stdout.strip().partition("/")
    kfps = round(float(pay) / float(bol or 1))
    pson = "" if kfps == 25 else f"_{kfps}"
    print(f"kaynak fps : {kfps}   partikul dongusu: arka{pson}.mp4 / on{pson}.mp4")

    gerekli = [VAR / "partikul" / f"arka{pson}.mp4", VAR / "partikul" / f"on{pson}.mp4",
               VAR / "mask_alt16.png"]
    for p in gerekli:
        if not p.exists():
            sys.exit(f"HATA: varlik eksik -> {p}\n"
                     f"  {kfps} fps dongu icin: python scripts\\video_fps.py --partikul --uret")

    if a.klip:
        kaynaklar = [p for p in kaynaklar if p.name.startswith(a.klip + "__")]
    bitmis = yapildi()

    if a.durum:
        print(f"kaynak {len(kaynaklar)} · islenmis {len(bitmis)} · kalan "
              f"{len([p for p in kaynaklar if p.name.split('__')[0] not in bitmis])}")
        return

    CIK.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    kalan = [p for p in kaynaklar
             if a.zorla or p.name.split("__")[0] not in bitmis]

    print(f"islenecek: {len(kalan)} / {len(kaynaklar)}")
    if not a.uret:
        print("\nKURU CALISMA — hicbir sey uretilmedi. Uretmek icin: --uret")
        if kalan:
            print("\nornek komut:")
            k = kalan[0]
            kod = k.name.split("__")[0]
            print("  " + " ".join(komut(k, CIK / f"{kod}__post.mp4",
                                        karakter(kod), pson)[:14]) + " …")
        return

    t0 = time.time()
    for i, kaynak in enumerate(kalan, 1):
        kod = kaynak.name.split("__")[0]
        hedef = CIK / f"{kod}__post.mp4"
        kar = karakter(kod)
        print(f"\n[{i}/{len(kalan)}] {kod}  ({kar})")
        r = subprocess.run(komut(kaynak, hedef, kar, pson))
        if r.returncode != 0 or not hedef.exists():
            sys.exit(f"HATA: {kod} islenemedi (donus {r.returncode}). Parti durduruldu.")
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kod": kod, "cikti": hedef.name,
                                "boyut": hedef.stat().st_size,
                                "zaman": time.strftime("%Y-%m-%dT%H:%M:%S")},
                               ensure_ascii=False) + "\n")

    dk = (time.time() - t0) / 60
    print(f"\nBITTI · {len(kalan)} klip · {dk:.1f} dakika")
    print("\nSIRADAKI ADIMLAR (elle):")
    print("  1. kiosk/videolar hardlink'lerini video/03_post'a tasi")
    print("  2. python scripts\\kiosk_build.py   (sureler ffprobe ile YENIDEN olculur)")
    print("  3. kiosk/data.js icinde mediaExt '.mp4' oldugunu dogrula")


if __name__ == "__main__":
    main()
