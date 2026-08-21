# Nizamiye web sitesi icin QR uretir ve kiosk config'ine URL'i isler.
# Kullanim:
#   python scripts/kiosk_qr.py                 -> yerel ag IP'sini bulur, http://IP:4321/
#   python scripts/kiosk_qr.py https://....    -> verilen adresi kullanir (canliya gecince)
# Cikti: kiosk/img/qr.svg + kiosk/data.js icindeki "webUrl" guncellenir.
import re
import socket
import sys
from pathlib import Path

import qrcode
import qrcode.image.svg

ROOT = Path(__file__).parent.parent
PORT = 4321

def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # paket gitmez; sadece yerel arayuzu secer
        return s.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()

url = sys.argv[1] if len(sys.argv) > 1 else f"http://{lan_ip()}:{PORT}/"

# NOT: QR'in kac kez OKUTULDUGU kiosktan olculemez (okutma telefonda olur).
# Olcmek isteyen QR adresine "?k=kiosk" ekler; site o parametreyi gorunce
# analitige "qr_ile_geldi" yazar (kod hazir, kiosk/analitik.js + Base.astro).
# Ama bu yalnizca site YEREL AGDAN servis edilirse ise yarar; canli alan adinda
# ziyaretcinin tarayicisi yerel toplayiciya erisemez. 21 Agu 2026: kullanici
# istemedi, adres temiz birakildi.
qr_url = url

img = qrcode.make(qr_url, image_factory=qrcode.image.svg.SvgPathImage,
                  box_size=14, border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
out = ROOT / "kiosk" / "img" / "qr.svg"
img.save(out)

data = ROOT / "kiosk" / "data.js"
t = data.read_text(encoding="utf-8")
t, n = re.subn(r'"webUrl":\s*"[^"]*"', f'"webUrl": "{url}"', t)
if n == 0:
    sys.exit("HATA: data.js icinde webUrl alani yok — once scripts/kiosk_build.py calistirin.")
data.write_text(t, encoding="utf-8")
print(f"QR: {out}\nEkranda: {url}\nQR icinde: {qr_url}\ndata.js guncellendi.")
