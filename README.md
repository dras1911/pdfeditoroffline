# PDF Tools — offline

Wewnętrzne narzędzie do obróbki PDF: reorder stron (drag-drop), rotate, usuwanie pustych stron (auto-detect), merge, obrazy→PDF, kompresja (Ghostscript + rasteryzacja).

**Bez autoryzacji** (sieć wewnętrzna / VPN). Szyfrowanie plików at-rest (Fernet). Brak internet egress. Brak telemetrii.

---

## Szybki start na Linuxie (Ubuntu 22.04 / 24.04) — Docker

Najprostsza ścieżka: wszystko w kontenerach, jedna komenda.

### 1. Wymagania

```bash
sudo apt update
sudo apt install -y docker.io docker-compose-plugin git
sudo usermod -aG docker $USER
newgrp docker          # odśwież grupę w bieżącej powłoce
```

### 2. Pobranie projektu

```bash
sudo mkdir -p /opt/pdftools && sudo chown $USER:$USER /opt/pdftools
cd /opt/pdftools
# wariant A: skopiuj całe repo (rsync/scp z Windows)
# wariant B: git clone <twoje-repo> .
```

### 3. Konfiguracja (`backend/.env`)

```bash
cp backend/.env.example backend/.env

# wygeneruj klucz szyfrujący — wklej do .env jako PDFTOOLS_ENCRYPTION_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

nano backend/.env
```

Minimalny `backend/.env`:

```
PDFTOOLS_ENCRYPTION_KEY=<klucz-z-komendy-wyżej>
PDFTOOLS_STORAGE_DIR=/var/lib/pdftools/sessions
PDFTOOLS_DB_PATH=/var/lib/pdftools/meta.db
PDFTOOLS_GHOSTSCRIPT_BIN=gs
PDFTOOLS_MAX_UPLOAD_MB=200
PDFTOOLS_TTL_HOURS=24
```

### 4. Start

```bash
docker compose up -d --build
```

Po ~2 min UI dostępne pod: **http://<ip-serwera>/** (nginx na porcie 80 → proxy `/api` do backendu).

Sprawdź:

```bash
docker compose ps
docker compose logs -f backend
curl http://localhost/api/health
```

### 5. Auto-start po reboocie (systemd)

```bash
sudo tee /etc/systemd/system/pdftools.service >/dev/null <<'EOF'
[Unit]
Description=PDF Tools
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/pdftools
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now pdftools
```

### 6. Aktualizacja

```bash
cd /opt/pdftools
git pull                         # lub rsync nowej wersji
docker compose up -d --build
```

### 7. Backup / czyszczenie

- Dane w woluminie `pdftools_data` (`docker volume inspect pdftools_data`).
- APScheduler co 30 min czyści pliki starsze niż `PDFTOOLS_TTL_HOURS` (domyślnie 24h).
- Ręczne czyszczenie wszystkiego: `docker compose down -v` **(kasuje wolumin z PDF-ami!)**.

---

## Szybki start — bez Dockera (bare-metal)

```bash
sudo apt install -y python3.12 python3.12-venv ghostscript nodejs npm nginx

# backend
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
export PDFTOOLS_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export PDFTOOLS_STORAGE_DIR=$HOME/.pdftools/sessions
export PDFTOOLS_DB_PATH=$HOME/.pdftools/meta.db
mkdir -p $HOME/.pdftools
uvicorn app.main:app --host 0.0.0.0 --port 8000

# frontend (drugi terminal)
cd frontend
npm ci
npm run build
# serve np. nginx-em z frontend/dist, proxy_pass /api → http://127.0.0.1:8000
```

---

## Architektura

- `backend/` — FastAPI, pypdf, PyMuPDF (fitz), img2pdf, Ghostscript, SQLModel/SQLite
- `frontend/` — React + Vite + TypeScript, react-pdf, @dnd-kit
- `docker-compose.yml` — `backend` (uvicorn) + `frontend` (nginx + SPA + reverse proxy `/api`)
- Storage: wolumin `pdftools_data` → `/var/lib/pdftools/{sessions,meta.db}`

## Offline — checklist

- ✅ Wszystkie zależności w obrazach Docker.
- ✅ pdf.js worker bundlowany lokalnie (vite import).
- ✅ Brak fontów Google / CDN / telemetrii.

### Transfer obrazów na maszynę bez internetu

```bash
# na maszynie z internetem
docker compose build
docker save pdftools-backend pdftools-frontend | gzip > pdftools-images.tar.gz
scp pdftools-images.tar.gz user@serwer:/opt/pdftools/

# na serwerze offline
cd /opt/pdftools
gunzip -c pdftools-images.tar.gz | docker load
docker compose up -d
```

## Bezpieczeństwo (RODO / dane medyczne)

- Pliki szyfrowane Fernet (`chmod 600 backend/.env`).
- Wolumin Docker na partycji LUKS (zalecane).
- TTL purge 24h (overwrite + unlink).
- **Brak auth** — chroń dostęp siecią (VPN / VLAN / reverse proxy z basic-auth / wewnętrzny nginx z TLS).

### TLS

Dodaj `listen 443 ssl;` w `frontend/nginx.conf`, podmontuj cert wewnętrznego CA volume-em.

## Endpointy API

| Method | Path | Opis |
|--|--|--|
| POST   | `/api/docs` | upload PDF |
| POST   | `/api/docs/from-images` | obrazy (jpg/png/tif/bmp/webp) → PDF |
| POST   | `/api/docs/merge` | scal wiele PDF (`{ids:[...]}`) |
| GET    | `/api/docs` | lista |
| DELETE | `/api/docs/{id}` | usuń |
| GET    | `/api/docs/{id}/blanks` | analiza pustych stron |
| POST   | `/api/docs/{id}/edit` | reorder/rotate/delete/compress |
| POST   | `/api/docs/{id}/remove-blanks` | auto-usuń puste |
| POST   | `/api/docs/{id}/compress` | tylko kompresja |
| GET    | `/api/docs/{id}/file` | pobierz PDF |
| GET    | `/api/docs/{id}/export-images?fmt=png&dpi=150` | eksport stron jako obrazy (ZIP) |
| GET    | `/api/health` | healthcheck |

### Payload `/edit`

```json
{
  "keep_order": [0, 2, 1, 3],
  "rotations": {"2": 90},
  "compress": true,
  "gs_quality": "high"
}
```

## Blank-detect

1. Tekst > 5 znaków → nie pusta.
2. Render 100 DPI greyscale → std-dev pikseli < 3.0 → pusta.

Tunable w `.env`: `PDFTOOLS_BLANK_TEXT_THRESHOLD`, `PDFTOOLS_BLANK_PIXEL_STD_THRESHOLD`, `PDFTOOLS_RENDER_DPI`.

## Kompresja — presety

| Preset | DPI obrazów | JPEG q | Zastosowanie |
|--|--|--|--|
| `low`       | 200 | 85 | Minimalna strata jakości |
| `medium`    | 150 | 75 | Balans (domyślnie) |
| `high`      | 100 | 60 | Agresywne, dokumenty biurowe |
| `extreme`   | 72  | 45 | Max w granicach Ghostscript |
| `rasterize` | 100 | 55 | Każda strona → JPEG → PDF. **Traci warstwę tekstu** — dla skanów daje największą redukcję. |

Stare wartości `/screen`, `/ebook`, `/printer`, `/prepress` też działają (mapują się na `-dPDFSETTINGS`).

Backend zwraca skompresowany PDF **tylko jeśli wynik jest mniejszy** od źródła.

## Dev lokalny (Windows)

```
dev-start.bat
```

Backend (uvicorn --reload @127.0.0.1:8000) + frontend (vite @localhost:5173) w osobnych oknach. Wymaga Python 3.12, Node 20+, Ghostscript w PATH.

## Testy

```bash
cd backend
pytest             # 22 testy, ~2s
```

## CI

GitHub Actions (`.github/workflows/ci.yml`):
- Backend: pytest + coverage (Ubuntu 24.04, Python 3.12, Ghostscript)
- Frontend: `npm run build` (Node 20)
- Docker: build obu obrazów

## Troubleshooting

| Objaw | Fix |
|--|--|
| `Failed to load PDF file` w UI | Wersje `pdfjs-dist` ≠ bundled w `react-pdf`. Zamiast `npm install` użyj `npm ci`. |
| `gs: command not found` | `sudo apt install ghostscript` lub ustaw `PDFTOOLS_GHOSTSCRIPT_BIN`. |
| Kompresja nie zmniejsza pliku | Plik już zoptymalizowany — spróbuj presetu `high` / `extreme` / `rasterize`. |
| `413 Request Entity Too Large` | Zwiększ `PDFTOOLS_MAX_UPLOAD_MB` **oraz** `client_max_body_size` w `frontend/nginx.conf`. |
| Backend nie startuje: `Fernet key must be 32 url-safe base64` | Źle wygenerowany/wklejony `PDFTOOLS_ENCRYPTION_KEY`. Użyj komendy z sekcji 3. |
| `Permission denied` przy wolumnie | `sudo chown -R 1000:1000 /var/lib/docker/volumes/pdftools_data/_data`. |
