# manifest.csv -> kiosk/data.js  (karakterler, kategoriler, sorular, sureler, intro/outrolar)
import csv
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.parent
KIOSK = ROOT / "kiosk"
KIOSK.mkdir(exist_ok=True)


def gercek_sure(kod: str) -> float | None:
    """Uretilmis medyanin OLCULEN suresi. Yoksa None.

    Onemli: kiosk oynaticisi bu degeri bekci zamanlayicisinda kullaniyor
    (index.html -> setTimeout(..., (dur + 8) * 1000)). manifest.csv'deki
    'tahmini_sn' karakter sayisindan hesaplaniyor ve gercekten 10 sn'ye kadar
    sapabiliyor; sapma 8 sn'yi gecince bekci klibi BITMEDEN kesiyor.
    Bu yuzden dosya varsa daima olculen sure kullanilir.
    """
    for alt, uzanti in (("videolar", ".mp4"), ("sesler", ".mp3")):
        p = KIOSK / alt / f"{kod}{uzanti}"
        if not p.exists():
            continue
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", str(p)],
                capture_output=True, text=True, timeout=30)
            return round(float(r.stdout.strip()), 1)
        except (ValueError, OSError, subprocess.SubprocessError):
            return None
    return None

META = {
    "gazali": {"name": "İmam Gazâlî", "title": "Hüccetü'l-İslâm · Baş Müderris"},
    "nizamulmulk": {"name": "Nizamülmülk", "title": "Büyük Selçuklu Veziri"},
    "meliksah": {"name": "Sultan Melikşah", "title": "Büyük Selçuklu Hükümdarı"},
}
CATS = {
    "gazali": {1: "Hayat & Medrese", 2: "İlim & Öğrenme", 3: "Kalp & Nasihat"},
    "nizamulmulk": {1: "Medreseler & Eğitim", 2: "Devlet & Adalet", 3: "Sultanlık & Dönem"},
    "meliksah": {1: "Saltanat & Devlet", 2: "İlmin Himayesi", 3: "Sultanın Dünyası"},
}
ORDER = ["meliksah", "nizamulmulk", "gazali"]

rows = list(csv.DictReader(open(ROOT / "senaryolar" / "manifest.csv", encoding="utf-8-sig")))
chars: dict[str, dict] = {
    c: {"id": c, **META[c],
        # hedcut kurali: her kullanim boyu icin ayri uretim (b=buyuk, o=orta, k=kucuk)
        "img": f"img/{c}_o.svg", "imgB": f"img/{c}_b.svg", "imgK": f"img/{c}_k.svg",
        "intros": [], "outros": [],
        "categories": [{"name": CATS[c][k], "questions": []} for k in (1, 2, 3)]}
    for c in ORDER
}
qmap: dict[tuple, dict] = {}
tahmine_dusen: list[str] = []
for r in rows:
    kod = r["kod"]
    dur = gercek_sure(kod)
    if dur is None:                      # medya henuz uretilmemis -> tahmine dus
        dur = float(r["tahmini_sn"])
        tahmine_dusen.append(kod)
    ch = kod.split("_")[0]
    item = {"code": kod, "dur": dur}
    if "_int_" in kod:
        chars[ch]["intros"].append(item)
    elif "_out_" in kod:
        chars[ch]["outros"].append(item)
    else:
        m = re.match(r"^[a-z]+_k(\d)_s(\d)(?:_v(\d))?$", kod)
        k, s = int(m.group(1)), int(m.group(2))
        key = (ch, k, s)
        if key not in qmap:
            qmap[key] = {"q": r["soru"], "variants": []}
            chars[ch]["categories"][k - 1]["questions"].append(qmap[key])
        qmap[key]["variants"].append(item)

data = {
    "config": {
        "pin": "1206", "rights": 3, "idleMs": 75000, "thanksMs": 20000,
        # Yollar kiosk klasorune GORECE (hem file:// hem Vercel'de calisir; Root Dir = kiosk)
        "skipAfterMs": 6000, "mediaBase": "sesler/", "mediaExt": ".mp3",
        "videoBase": "videolar/", "videoExt": ".mp4",
        # Canli site adresi (QR bununla gosterilir). Ozel durum icin kiosk_qr.py override eder.
        "webUrl": "https://nizamiyeweb.vercel.app/",
    },
    "characters": [chars[c] for c in ORDER],
}
out = KIOSK / "data.js"
out.write_text("window.KIOSK_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")
nq = sum(len(cat["questions"]) for c in data["characters"] for cat in c["categories"])
nv = sum(len(q["variants"]) for c in data["characters"] for cat in c["categories"] for q in cat["questions"])
print(f"yazildi: {out} | {len(data['characters'])} karakter, {nq} soru, {nv} cevap klibi")
if tahmine_dusen:
    print(f"UYARI: {len(tahmine_dusen)} klipte medya bulunamadi, tahmini sure kullanildi:")
    print("  " + ", ".join(tahmine_dusen))
else:
    print("tum sureler medyadan OLCULDU (bekci zamanlayicisi icin kritik)")
