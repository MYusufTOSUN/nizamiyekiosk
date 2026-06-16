# Test ses fixture'ları

`tests/integration/test_whisper_local.py` ve `scripts/benchmark_stt.py` bu
klasördeki `.wav` dosyalarını kullanır. Yoksa GPU testleri SKIP edilir.

## Gerekli dosyalar

Bu klasöre **5 Türkçe ses örneği** koy (16 kHz mono WAV, temiz kayıt):

| Dosya | İçerik | Süre |
|-------|--------|------|
| `kisa.wav` | "Fil saati nedir?" | ~2 sn |
| `orta.wav` | "Cezeri bana atölyeni anlatır mısın?" | ~4 sn |
| `uzun.wav` | "Robotların nasıl çalıştığını ve senin makinelerinin..." | ~10 sn |
| `cocuk.wav` | Bir çocuğun sesiyle kısa soru (aksan/tonalite testi) | ~3 sn |
| `gurultulu.wav` | Arka planda gürültü olan bir soru | ~4 sn |

## Nasıl üretilir

- Telefonla kaydet → `ffmpeg -i input.m4a -ar 16000 -ac 1 kisa.wav`
- Veya `scripts/test_microphone.py` mikrofon kaydını WAV'a dök
- Veya TTS ile sentetik üret (gerçek mikrofon kalitesini tam yansıtmaz)

## Neden gitignore'da

WAV dosyaları repoya konmaz (boyut + telif). Her klonda yerel üretilir.
Beklenen transcript'leri `expected.json` içine yaz (WER ölçümü için):

```json
{ "kisa.wav": "fil saati nedir", "orta.wav": "cezeri bana atölyeni anlatır mısın" }
```
