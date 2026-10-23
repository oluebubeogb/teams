# Teams — Suite org sync

- New `app/routers/suite.py`: `GET /api/suite/orgs` (orgs + members, `since` delta) and `POST /api/suite/orgs/upsert` (idempotent on source+external_id), protected by `SUITE_SYNC_TOKEN` (constant-time compare; 503 when unset).
- Migration `002_suite_sync.sql`: `orgs.source` (default `teams`), `external_id`, `org_type`, `updated_at` + triggers so org and membership edits bump `updated_at`; unique `(source, external_id)`.
- Orgs created in Crede appear in Teams like any other org (`source: "crede"` in the org API). Owners who never opened Teams get a lightweight user mirror that their first SSO sign-in adopts (matched by Accounts id or email); other members get email invites.
- `SUITE_SYNC_TOKEN` added to config, docker-compose.yml and .env.example.
- Smoke test: +8 suite checks; full run 88 passed, 0 failed.
