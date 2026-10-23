# Teams v2

A place for people and organizations to publish documents and be discovered. Teams is part of the Collab Suite and signs in through Collab Accounts.

- **Feed.** For You, Following and Latest, with rich cards that show a first-page preview. On mobile there is a full-screen viewer you swipe up and down through.
- **Search.** One search box covers file *contents* (PDF, DOCX, PPTX, XLSX, CSV, TXT, MD), people, orgs and topics.
  - It uses weighted Postgres full-text search plus `pg_trgm` for typos.
  - Results come with highlighted snippets.
  - Results respect permissions: private, org-only and link-only files never leak.
- **Orgs.** Each org is owned by a user. Roles are owner, admin, editor and member. You can invite people by username or email, and every org has a `/o/<slug>` page with its own avatar and cover.
  - **Curation by consent.** An org can ask to feature someone else's file. The file appears on the org page only after the author approves.
- **Social.**
  - Threaded comments: one visible level, with collapsed replies.
  - @mentions, likes, saves and follows of people, orgs and topics.
  - A notification bell.
  - Sharing to WhatsApp, X, LinkedIn, Telegram and Facebook, plus copy link and the native share sheet. Pages carry OG meta.
- **Counting.** Views, downloads and shares are deduped per visitor per day, and the source is recorded (feed, search, org, topic, embed, or share:whatsapp…).
- **Top lists.** Overall or per topic, for the day, week, month or all time.
- **Bulk.**
  - Select mode supports checkboxes, shift-click and long-press on mobile.
  - Bulk actions: visibility, tags, org, collaborators, delete and zip download.
  - The bulk upload queue shows progress per file and applies shared metadata.
- **Analytics.** Dashboards for you, each org (editor and above) and the whole site (admin). Drill down org → file → day → source, with hand-rolled SVG charts.
- **Extras.**
  - Collections, file versions and an embed player.
  - A verified badge for orgs.
  - Reporting with auto-hide, plus an admin moderation queue.
  - Optional AI tagging and summaries through any OpenAI-compatible endpoint.
- **UI.**
  - A self-hosted design system with no CDN, colour-coded by function: files indigo, social rose, orgs green, analytics amber.
  - Dark and light themes, skeleton loaders, micro-animations and tooltips.
  - A native-feeling mobile layout: bottom tab bar, sheets and safe areas.
  - An installable PWA.

The stack is FastAPI, PostgreSQL 16 (psycopg 3, pooled) and vanilla JS with no build step. Storage is local disk or any S3-compatible bucket. Previews come from poppler (PDF) and LibreOffice (Office files).

## Run locally

```bash
python -m venv venv && . venv/bin/activate && pip install -r requirements.txt
createdb teams   # or: docker run -e POSTGRES_PASSWORD=teams -e POSTGRES_USER=teams -p 5432:5432 postgres:16-alpine
export DATABASE_URL=postgresql://teams:teams@localhost:5432/teams AUTH_MODE=local COOKIE_SECURE=false \
       DATA_DIR=./data APP_URL=http://localhost:8008 ADMIN_USERNAMES=ada
uvicorn app.main:app --reload --port 8008     # migrations run automatically on boot
python scripts/seed.py --history               # demo people, orgs, documents, 45 days of analytics
python scripts/smoke_test.py                   # ~80 end-to-end checks
```

In `AUTH_MODE=local` you sign in with an email address only (the account is created on first use), which is handy for demos. Production uses `AUTH_MODE=accounts`.

## Deploy on Coolify

1. **Create the resource.** New resource → Docker Compose → point it at this repo. Coolify reads `docker-compose.yml`, which contains two services:
   - `app`: built from the Dockerfile, port 8008.
   - `db`: postgres:16-alpine.
2. **Set the domain.** On the **app** service, set the domain to `https://teams.collab.name.ng` with port **8008**. No host ports are published; Coolify's proxy handles routing.
3. **Fill in the environment variables.** Every variable appears in the Environment Variables tab. At minimum, set:
   - `SECRET_KEY`: the **same** value as Collab Accounts, so SSO tokens verify.
   - `POSTGRES_PASSWORD`: a long random string.
   - `ADMIN_USERNAMES`: your Accounts username.
   - Optionally the `S3_*` variables, with `STORAGE_BACKEND=s3`, to keep files off the VPS disk.
4. **Deploy.** Migrations run on boot. `/health` is the healthcheck.
5. **Volumes.** `teams-data` holds uploads, previews and avatars. `teams-pg` holds the database. Back up both, or just `teams-pg` if you use S3.

### Migrating from Teams v1 (SQLite)

Copy the old `teams.db` and uploads folder into the running app container, or onto any machine that can reach the database. Then run:

```bash
python scripts/migrate_sqlite.py --sqlite /path/to/teams.db --uploads /path/to/old/app/uploads   # --dry-run first
```

How the migration behaves:
- Users are matched by username or email.
- Files become **public**, keep their view and download counts, and are fully re-indexed for search.
- Previews are generated in the background on the next boot.
- It is safe to re-run.

### Collab Accounts profile pictures

Teams shows the `avatar_url` returned by Accounts' `/auth/me`. If a user has no picture, Teams falls back to their initials and a colour. The matching Accounts change ships separately (`collab-accounts-avatar.zip`).

## Layout

```
app/
  main.py            pages, OG meta, PWA, health
  config.py          every setting (env)
  db.py              pool + migrations (app/migrations/*.sql)
  auth.py            Accounts SSO / local auth, cookies
  core.py            permissions (VIS), cards, events, notifications, background processing
  extract.py         text extraction + previews
  storage.py         local / S3
  ai.py              optional tagging
  routers/           files (+bulk), discover (feed/search/top/topics), social (+reports), orgs (+collections, verify), users, analytics
  static/            css/app.css (design system), js/app.js (SPA-ish pages), sw.js, img/
  templates/         page.html shell, icons.html sprite, embed
scripts/             seed.py, smoke_test.py, migrate_sqlite.py
```

## Collab Suite org sync (Crede ⇄ Teams)

Set the same long random `SUITE_SYNC_TOKEN` on Teams and Crede. Blank disables the endpoints (they return 503).

- `GET /api/suite/orgs?since=ISO&limit=200` — every org (any source) with members (`accounts_id`, `email`, `role`). `since` returns only orgs changed after that time (member changes count).
- `POST /api/suite/orgs/upsert` — create or update an org created in another app. Body: `source` (e.g. `crede`), `external_id`, `name`, `about`, `website`, `location`, `org_type` (`company|school|issuer`), `owner {accounts_id,email,full_name}`, `members[]`. Idempotent on `(source, external_id)`; slugs never collide. Members who have not used Teams yet get an email invite that attaches on first sign-in.

Auth: `Authorization: Bearer <SUITE_SYNC_TOKEN>` (or `X-Suite-Token`). Migration `002_suite_sync.sql` adds `orgs.source`, `external_id`, `org_type` and `updated_at`.
