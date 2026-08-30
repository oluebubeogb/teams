# Teams

Sleek, mobile-first file discovery & sharing platform.

Users can browse and search publicly. Only logged-in users can upload.  
Search groups results by file type ordered by keyword relevance in content.

## Features (v1)

- Sign up / Login / Forgot password (email + phone verification)
- Eye toggle on all password fields
- Day / Night theme toggle
- Responsive grid (1 → 2 → 3 columns)
- Public profiles at `/u/username`
- User dashboard with bio
- Upload: PDF, DOCX, PPTX, XLSX, CSV + URL links
- Description max 50 characters
- Content-aware search with grouped results
- Author initials avatar, relative dates, view & download
- Clean SVGs, consistent font size, weight hierarchy
- **Dockerfile-only** (Coolify ready)

## Project structure

```
app/
  main.py          # FastAPI app + all routes
  auth.py          # JWT + password helpers
  database.py      # SQLite
  models.py        # Pydantic schemas
  utils.py         # initials, relative date, text extraction, scoring
  templates/       # Jinja2 + Tailwind CDN
  static/
  uploads/         # created at runtime
Dockerfile
requirements.txt
.env.example
```

## Local run (optional)

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # edit SECRET_KEY
uvicorn app.main:app --reload --host 0.0.0.0 --port 8008
```

Open http://localhost:8008

## Deploy on Coolify (Dockerfile only)

This project is **Dockerfile-only**. Do **not** use Docker Compose.

### 1. Push to Git

```bash
git init
git add .
git commit -m "Teams v1 — Dockerfile only"
git remote add origin <your-repo-url>
git push -u origin main
```

### 2. Create resource in Coolify

1. **New Resource** → **Dockerfile** (or **Public Repository** / Git and choose build pack **Dockerfile**).
2. Connect your Git repository.
3. Coolify will detect the root `Dockerfile`.
4. **Port**: `8008` (container port). Coolify will map domain → 8008.
5. **Environment variables** (required):

| Variable     | Value                                      |
|--------------|--------------------------------------------|
| `SECRET_KEY` | long random string (e.g. `openssl rand -hex 32`) |

Optional:

| Variable     | Default        | Notes                |
|--------------|----------------|----------------------|
| `UPLOAD_DIR` | `app/uploads`  | leave default        |

6. **Persistent storage** (important so DB + files survive redeploys):

   In Coolify → **Storages** / **Volumes**, add **directory** mounts:

   | Destination (container path) | Purpose              |
   |------------------------------|----------------------|
   | `/app/data`                  | SQLite DB (`teams.db`) |
   | `/app/app/uploads`           | Uploaded files       |

   Use **directory** volumes only. Do **not** mount a volume onto a single file path like `/app/teams.db` — that causes `unable to open database file`.

7. **Healthcheck**: Dockerfile already includes one hitting `/health`.

8. Deploy.

### 3. After deploy

- Open your domain.
- Sign up a user.
- Login → Dashboard should load (no Internal Server Error).
- Uploads and SQLite persist if volumes are set.

### Common Coolify pitfalls (fixed in this package)

- Do **not** select Docker Compose build pack — there is no `docker-compose.yml`.
- Port must be **8008** (matches `EXPOSE` and `CMD`).
- Set `SECRET_KEY` or sessions will be insecure / inconsistent across restarts.
- Without a volume on `/app/app/uploads` and `/app/teams.db`, data is lost on every redeploy.
- Template encoding issues that caused **500 Internal Server Error** on `/dashboard` after login are fixed.

## Notes

- Password minimum: 6 characters.
- Avatars are generated initials (no image upload in v1).
- URL uploads only store the link (no content scrape).
- Search ranks by simple keyword frequency in extracted text + filename + description.
- Empty file-type groups are hidden.

Built for Coolify on any VPS.
