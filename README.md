# Nizamiye Medresesi — Hologram Sergisi

Selçuklu'nun ilim ocağı Nizamiye medreselerini anlatan interaktif bir müze
sergisinin kaynak kodu.

Ziyaretçi dokunmatik bir kiosktan sorusunu seçiyor; cevabı, Pepper's Ghost
kabininde havada asılı duran tarihî figür veriyor.

**BilimFest Konya · 20 Ağustos 2026**

---

## Deneyim

Üç karakter — **Sultan Melikşah**, **Nizâmülmülk**, **İmam Gazâlî**. Her biri
kendi alanında soruları cevaplıyor: saltanat ve devlet, medreseler ve adalet,
ilim ve nasihat.

```
karakter seçimi → soru seçimi → hologramda cevap → teşekkür / QR
```

Ziyaretçi başına üç soru hakkı. Bir oturum ortalama 100 saniye sürüyor.

---

## Mimari

İki ekran, iki ayrı tarayıcı penceresi:

```
┌─ kiosk ekranı ──────────┐        ┌─ kabin ekranı ──────────┐
│  index.html             │        │  hologram.html          │
│  soru seçimi, sayaç,    │◄──────►│  tam siyah zemin,       │
│  operatör paneli        │  Broad-│  klip oynatma,          │
│                         │  cast  │  hizalama katmanı       │
└─────────────────────────┘ Channel└─────────────────────────┘
```

İki pencere `BroadcastChannel` ile haberleşiyor; aynı köken şartı olduğu için
yerel bir HTTP sunucusu üzerinden servis ediliyor. Hologram bağlıysa ses videodan
çalıyor — videonun kendi sesi olduğu için dudak senkronu tam; kiosktaki `<audio>`
yalnızca zamanlayıcı görevi görüyor ve video açılmazsa anında devreye giriyor.

Kabin ekranı açılışta ekranın tazeleme hızını ölçüyor ve klibin kare hızıyla tam
kat tutmuyorsa uyarıyor — 50 fps klip için 50 veya 100 Hz düzgün, 60 / 120 / 144
titriyor.

### Veri katmanı

Kiosk verisi elle düzenlenmiyor; senaryo kaynağından üretiliyor. Klip süreleri
`ffprobe` ile ölçülüyor, metin uzunluğundan tahmin edilmiyor.

### Üretim zinciri

```
senaryo metni ──► ElevenLabs ──► ses
                       │
                       └──► fal.ai / HeyGen avatar4 ──► ham klip
                                                            │
                                     ara kare üretimi (25 → 50 fps)
                                                            │
                              ışık / partikül / atmosfer + 3840×2160 tuval
```

Para harcayan her betik **varsayılan olarak kuru çalışıyor**; `--uret` bayrağı
olmadan tek istek gitmiyor. Üretim manifest tabanlı idempotent — tamamlanmış bir
klibe ikinci istek gitmiyor, herhangi bir 4xx'te tüm parti duruyor.

---

## İki çalışma kipi

**Önceden üretilmiş klipler** — festivalde kullanılan kip. Akış deterministik,
gecikme yok, içerik baştan denetlenmiş. Kalabalık ve gürültülü bir festival
ortamı için seçilen yol bu.

**Canlı sohbet** (`src/`) — ziyaretçinin serbest soru sorduğu kip:

```
mikrofon → STT → RAG → LLM → TTS → hologram
```

Çalışır durumda; kontrollü akustik ve moderasyon imkânı olan **kalıcı bilim
merkezi sergisi** için duruyor.

---

## Depo yapısı

| | |
|---|---|
| `kiosk/` | dokunmatik uygulama ve kabin ekranı |
| `scripts/` | üretim, post-prodüksiyon ve derleme betikleri |
| `src/` | canlı sohbet kipi |
| `tests/` | testler |
| `deployment/` | dağıtım yapılandırması |

Bu depo **yalnızca kodu** taşır. Senaryo metinleri, ses ve video kayıtları,
master görseller ve üretim belgeleri depoda bulunmaz.

---

## İçerik

Her tarihî bilgi **TDV İslâm Ansiklopedisi** kaynaklıdır. Olgu alınmış, cümle
alınmamıştır. Kaynakta bulunmayan kesinlik üretilmemiş; iki madde farklı değer
verdiğinde aralık verilmiştir.

> Sergideki figürler, sesler ve videolar **yapay zekâ ile üretilmiş temsilî
> canlandırmalardır.** Tarihî kişilerin gerçek görüntüsü ya da sesi değildir.

---

## Kardeş proje

[**nizamiyedebirgun.com**](https://nizamiyedebirgun.com) — üç dilli (TR / EN / AR)
dijital sergi. Ziyaretçi kabindeki QR ile buraya yönleniyor.
Depo: [nizamiyeweb](https://github.com/MYusufTOSUN/nizamiyeweb)

---

## Lisans

Kod **MIT** ile lisanslıdır — bkz. [LICENSE](LICENSE) ve [NOTICE.md](NOTICE.md).
