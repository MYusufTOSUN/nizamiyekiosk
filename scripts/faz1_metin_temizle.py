# -*- coding: utf-8 -*-
"""
FAZ 1 — TTS metin temizligi (0 kredi).
SADECE cevap/intro/outro GOVDE metinlerini degistirir.
'## kod' basliklarina ve '**Tip/Soru**' meta satirlarina DOKUNMAZ
  -> ekranda gorunen SORU metinleri korunur (imla bozulmaz).

Uygulanan kurallar (Faz 0 testleriyle dogrulanmis):
  1) Kesme isareti kaldirilir      : Bagdat'ta -> Bagdatta   (duraklama sebebi)
  2) Noktali virgul -> virgul      : ...actik; ... -> ...actik, ...   (DENEY 3)
  3) Sayilar yaziyla               : 1067'de -> bin altmis yedide
  4) Ozel isimlere diakritik       : Celali -> Celâlî  (telaffuz uzatmasi)
  5) Bosluklu isim birlestirme     : Alp Arslan -> Alparslan
  6) Kullanicinin icerik istekleri

Kullanim:  python scripts/faz1_metin_temizle.py          -> KURU (sadece diff)
           python scripts/faz1_metin_temizle.py --uygula -> yazar
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SEN = ROOT / "senaryolar"
UYGULA = "--uygula" in sys.argv

# --- 1) Once ozel/hedefli degisimler (sira onemli) ---
HEDEFLI = [
    # kullanicinin icerik istekleri
    ("duvarları değil, içindekiler yapar okulu.",
     "okulu okul yapan duvarları değil, içindekilerdir."),
    ("başlarında Ömer Hayyam",
     "başlarında Ömer Hayyâm olmak üzere"),
    ("Ona dediğimi size diyeyim.",
     "Ona dediğimi size de söyleyeyim."),
    # telaffuz hedefli
    ("Alp Arslan", "Alparslan"),          # bosluk -> duraklama
    ("ta Yemen'de", "tâ Yemende"),        # kelime yapismasi
    ("Hüccetü'l-İslam", "Hüccetül İslâm"),
    ("Eyyühe'l-Veled", "Eyyühel Veled"),
    ("el-Münkız mine'd-Dalal", "el Münkız mined Dalâl"),
    # sayilar (yaziyla)
    ("1067'de", "bin altmış yedide"),
    ("1058'de", "bin elli sekizde"),
    ("1072", "bin yetmiş iki"),
    ("1092", "bin doksan iki"),
]

# --- 1b) TELAFFUZ/ANLAM duzeltmeleri ---
# Onceki "â temizligi" EKRANDA GORUNEN metin icindi; TTS govdesini de vurdu ve
# bazi kelimelerin ANLAMI degisti. Govde ekranda gorunmedigi icin sapkalar geri konur.
ANLAM = [
    # gercek anlam hatalari (baglama duyarli — sadece dogru yerde)
    ("Kitaplarım hala konuşur", "Kitaplarım hâlâ konuşur"),   # hala=teyze -> hâlâ=halen
    ("tarlada hala kış", "tarlada hâlâ kış"),
    ("en karlı işi", "en kârlı işi"),                          # kârlı=kazancli ("karlı dağlar" DOKUNULMAZ)
    ("dahilerini", "dâhilerini"),                              # dâhi=deha (dahi=bile)
    ("inkarla", "inkârla"),
]
# kelime-sinirli (yanlis eslesme yok: 'zalimlerin' etkilenmez)
ANLAM_RE = [
    (r"\balim", "âlim"),        # alim, alimi, alimler...  ('zalim' iceren kelimeler haric
    (r"\bşikayet", "şikâyet"),
    (r"\bkağıt", "kâğıt"),
    (r"\bKağıt", "Kâğıt"),
]

# --- 2) Diakritik (TTS telaffuz uzatmasi icin; ekranda gorunmez) ---
DIAKRITIK = [
    ("Nizamülmülk", "Nizâmülmülk"),
    ("Nizamiye", "Nizâmiye"),
    ("Gazali", "Gazâlî"),
    ("Celali", "Celâlî"),
    ("Cüveyni", "Cüveynî"),
    ("Hayyam", "Hayyâm"),
    ("İmamü'l-Haremeyn", "İmâmül Haremeyn"),
    ("Siyasetname", "Siyâsetnâme"),
    ("İhya", "İhyâ"),
    ("İslam", "İslâm"),
]


HARF = "A-Za-zÇĞİIÖŞÜçğıöşüÂÎÛâîû"


def kesme_temizle(s: str) -> str:
    """Harf-kesme-harf kalibini birlestirir: Bagdat'ta -> Bagdatta (diakritikli harfler dahil)"""
    return re.sub(rf"(?<=[{HARF}])['’](?=[{HARF}])", "", s)


def cumle_basi_buyut(s: str) -> str:
    """Sayi->yazi donusumu sonrasi cumle basindaki 'bin ...' buyuk harfe cevrilir."""
    s = re.sub(r"^bin ", "Bin ", s)
    return re.sub(r"([.!?]\s+)bin ", lambda m: m.group(1) + "Bin ", s)


def govde_temizle(s: str) -> str:
    for a, b in HEDEFLI:
        s = s.replace(a, b)
    for a, b in ANLAM:
        s = s.replace(a, b)
    for pat, b in ANLAM_RE:
        s = re.sub(pat, b, s)
    for a, b in DIAKRITIK:
        s = s.replace(a, b)
    s = kesme_temizle(s)
    s = s.replace(";", ",")            # DENEY 3
    s = cumle_basi_buyut(s)
    s = re.sub(r"\s{2,}", " ", s)
    return s


toplam_degisen = 0
ornekler = []

for md in sorted(SEN.glob("*.md")):
    if md.name == "README.md":
        continue
    satirlar = md.read_text(encoding="utf-8").splitlines(keepends=True)
    yeni, degisen = [], 0
    for ln in satirlar:
        s = ln.rstrip("\n")
        # GOVDE degilse dokunma: baslik, meta, bos satir
        if not s or s.startswith("## ") or s.startswith("#") or s.startswith("**"):
            yeni.append(ln)
            continue
        t = govde_temizle(s)
        if t != s:
            degisen += 1
            if len(ornekler) < 6:
                # ilk farkli parcayi ornek olarak yakala
                for a, b in HEDEFLI + DIAKRITIK:
                    if a in s and b in t:
                        ornekler.append(f"    {a}  ->  {b}")
                        break
        yeni.append(t + ("\n" if ln.endswith("\n") else ""))
    if degisen:
        print(f"  {md.name}: {degisen} govde satiri degisecek")
        toplam_degisen += degisen
    if UYGULA:
        md.write_text("".join(yeni), encoding="utf-8")

print(f"\n{'YAZILDI' if UYGULA else 'KURU CALISMA (yazilmadi)'} — toplam {toplam_degisen} govde satiri")
if ornekler:
    print("\n  ornek donusumler:")
    for o in dict.fromkeys(ornekler):
        print(o)
if not UYGULA:
    print("\n  Yazmak icin: python scripts/faz1_metin_temizle.py --uygula")
