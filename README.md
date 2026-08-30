# Teams

Sleek, mobile-first file discovery & sharing platform.

Users can browse and search publicly. Only logged-in users can upload.  
Search groups results by file type ordered by keyword relevance in content.

## Features (v1)

- Sign up / Login / Forgot password (email + phone verification)
- Eye toggle on all password fields
- Day / Night theme toggle
- Responsive grid (1 ’ 2 ’ 3 columns)
- Public profiles at `/u/username`
- User dashboard with bio
- Upload: PDF, DOCX, PPTX, XLSX, CSV + URL links
- Description max 50 characters
- Content-aware search with grouped results
- Author initials avatar, relative dates, view & download
- Clean SVGs, consistent font size, weight hierarchy
- Coolify / Docker ready

## Quick Start (local)

```bash
# optional virtualenv
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8008
```

Open http://localhost:8008

## Deploy on Coolify

1. Create a new **Resource** ’ **Docker Compose** (or Dockerfile).
2. Connect your Git repository (this project).
3. Coolify will detect `Dockerfile` / `docker-compose.yml`.
4. Set environment variable:
   - `SECRET_KEY` = a long random string
5. Publish port **8008** (or let Coolify handle reverse proxy).
6. Deploy.

Persistent volumes are defined for uploads and the SQLite database.

For production you can later switch the SQLite path to a mounted volume or migrate to PostgreSQL.

## Git

```bash
git init
git add .
git commit -m "Teams v1  sleek file discovery"
git remote add origin git@github.com:oluebubeogb/teams.git
git push -u origin main
```

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
  uploads/
Dockerfile
docker-compose.yml
requirements.txt
```

## Notes

- Password minimum: 6 characters (any length above that).
- Avatars are generated initials (no image upload in v1).
- URL uploads only store the link (no content scrape).
- Search ranks by simple keyword frequency in extracted text + filename + description.
- Empty file-type groups are hidden.
- First group shown is the one with the highest relevance score.

Built for Coolify on any VPS.
