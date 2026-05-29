# BilimFest — Festival Deployment

Festival makinesinde sistemin **kesintisiz 28 saat** çalışması beklenir.
Bu klasör operasyonel kurulum dosyalarını içerir.

## Hızlı Kurulum (Linux server)

```bash
# 1) Kullanıcı + dizin
sudo useradd -r -s /bin/false festival
sudo mkdir -p /opt/bilimfest /var/log/bilimfest
sudo chown -R festival:festival /opt/bilimfest /var/log/bilimfest

# 2) Kod + venv
sudo -u festival git clone https://github.com/MYusufTOSUN/bilimfest.git /opt/bilimfest
cd /opt/bilimfest
sudo -u festival python3.11 -m venv .venv
sudo -u festival .venv/bin/pip install -e ".[stt,llm,tts]"
# (Phase 3 README'ye göre CUDA + GGUF + embeddings indir)

# 3) systemd
sudo cp deployment/systemd/bilimfest.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bilimfest

# 4) Durum
sudo systemctl status bilimfest
sudo journalctl -u bilimfest -f
```

## Monitoring

### Prometheus

```bash
docker run -d --name prometheus -p 9090:9090 \
  -v $PWD/deployment/monitoring/prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus:latest
```

### Grafana

```bash
docker run -d --name grafana -p 3000:3000 grafana/grafana:latest
# Web UI → http://localhost:3000 → "Add data source" Prometheus http://host:9090
# Import → deployment/monitoring/grafana_dashboard.json
```

## Festival Öncesi Sağlık Kontrolü

```bash
# Tüm bileşenler hazır mı?
curl http://localhost:8000/health             # → {"status":"ok"}
curl http://localhost:8000/api/v1/characters  # → [{"id":"cezeri",...}]
curl http://localhost:8000/api/v1/status      # → state

# Sahte E2E
python scripts/test_e2e.py --host http://localhost:8000

# Latency
python scripts/benchmark_stt.py
python scripts/test_llm.py "Fil saati nedir?"
python scripts/test_tts.py "Aleyküm selam" --out /tmp/x.wav
```

## Acil Durumlar

```bash
# Sergiyi anında durdur
curl -X POST http://localhost:8000/api/v1/emergency_stop

# Yeniden başlat
sudo systemctl restart bilimfest

# Aktif oturumu manuel bitir
curl -X POST http://localhost:8000/api/v1/session/end
```

## Bilinen Sınırlar

- Llama 3.1 8B Q4 zaman zaman persona kurallarını gevşek tutuyor — modern
  konular için fabricasyon görülür. 70B'ye geçildiğinde düzelir.
- Tek karakter (Cezerî) ile sergi gidiyor. Diğer karakterler için
  `src/llm/persona/<id>.py` + `src/llm/responses/<id>.json` ve
  `data/voices/<id>/ref_*.wav` eklenmeli, sonra `make build-rag`.
- Audio2Face yoksa lipsync mock; ağız sallanır ama fonem hizalı değil.

## İlgili Dosyalar

- `systemd/bilimfest.service` — service unit dosyası
- `monitoring/prometheus.yml` — scrape config
- `monitoring/grafana_dashboard.json` — yedek dashboard
