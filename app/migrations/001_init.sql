-- Teams v2 schema. Idempotent: safe to run on an existing database.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

CREATE TABLE IF NOT EXISTS users (
  id            BIGSERIAL PRIMARY KEY,
  accounts_id   TEXT UNIQUE,
  username      TEXT UNIQUE NOT NULL,
  full_name     TEXT NOT NULL DEFAULT '',
  email         TEXT UNIQUE NOT NULL,
  phone         TEXT,
  bio           TEXT NOT NULL DEFAULT '',
  avatar_url    TEXT,
  avatar_color  TEXT,
  password_hash TEXT NOT NULL DEFAULT '',
  is_admin      BOOLEAN NOT NULL DEFAULT false,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS users_name_trgm ON users USING gin ((lower(full_name || ' ' || username)) gin_trgm_ops);

CREATE TABLE IF NOT EXISTS orgs (
  id          BIGSERIAL PRIMARY KEY,
  slug        TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  tagline     TEXT NOT NULL DEFAULT '',
  about       TEXT NOT NULL DEFAULT '',
  website     TEXT NOT NULL DEFAULT '',
  location    TEXT NOT NULL DEFAULT '',
  owner_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  avatar_key  TEXT,
  cover_key   TEXT,
  accent      TEXT,
  verified    BOOLEAN NOT NULL DEFAULT false,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orgs_name_trgm ON orgs USING gin ((lower(name || ' ' || slug)) gin_trgm_ops);

CREATE TABLE IF NOT EXISTS org_members (
  org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role        TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','admin','editor','member')),
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','invited')),
  title       TEXT NOT NULL DEFAULT '',
  invited_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (org_id, user_id)
);
CREATE INDEX IF NOT EXISTS org_members_user ON org_members(user_id, status);

-- Invites for people who have not opened Teams yet (matched by email on first sign-in)
CREATE TABLE IF NOT EXISTS org_email_invites (
  id          BIGSERIAL PRIMARY KEY,
  org_id      BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  email       TEXT NOT NULL,
  role        TEXT NOT NULL DEFAULT 'member',
  invited_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (org_id, email)
);

CREATE TABLE IF NOT EXISTS files (
  id             BIGSERIAL PRIMARY KEY,
  user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  org_id         BIGINT REFERENCES orgs(id) ON DELETE SET NULL,
  visibility     TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public','org','private','link')),
  link_token     TEXT NOT NULL DEFAULT md5(random()::text || clock_timestamp()::text),
  filename       TEXT NOT NULL,
  original_name  TEXT NOT NULL,
  file_type      TEXT NOT NULL,
  storage_key    TEXT,
  url            TEXT,
  size_bytes     BIGINT NOT NULL DEFAULT 0,
  page_count     INT,
  thumb_key      TEXT,
  thumb_status   TEXT NOT NULL DEFAULT 'pending',
  title          TEXT NOT NULL DEFAULT '',
  description    TEXT NOT NULL DEFAULT '',
  tags           TEXT[] NOT NULL DEFAULT '{}',
  extracted_text TEXT NOT NULL DEFAULT '',
  ai_summary     TEXT,
  version        INT NOT NULL DEFAULT 1,
  hidden         BOOLEAN NOT NULL DEFAULT false,
  allow_comments BOOLEAN NOT NULL DEFAULT true,
  allow_download BOOLEAN NOT NULL DEFAULT true,
  view_count     INT NOT NULL DEFAULT 0,
  download_count INT NOT NULL DEFAULT 0,
  share_count    INT NOT NULL DEFAULT 0,
  like_count     INT NOT NULL DEFAULT 0,
  comment_count  INT NOT NULL DEFAULT 0,
  save_count     INT NOT NULL DEFAULT 0,
  search_vec     TSVECTOR,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS files_user ON files(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS files_org ON files(org_id, created_at DESC);
CREATE INDEX IF NOT EXISTS files_latest ON files(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS files_search ON files USING gin(search_vec);
CREATE INDEX IF NOT EXISTS files_title_trgm ON files USING gin ((lower(title)) gin_trgm_ops);
CREATE INDEX IF NOT EXISTS files_tags ON files USING gin(tags);

-- Weighted full-text vector: A=title, B=tags, C=description/summary, D=file content
CREATE OR REPLACE FUNCTION files_search_vec_update() RETURNS trigger AS $$
BEGIN
  NEW.search_vec :=
      setweight(to_tsvector('english', coalesce(NEW.title,'')), 'A')
   || setweight(to_tsvector('english', coalesce(array_to_string(NEW.tags, ' '),'')), 'B')
   || setweight(to_tsvector('english', coalesce(NEW.description,'') || ' ' || coalesce(NEW.ai_summary,'') || ' ' || coalesce(NEW.original_name,'')), 'C')
   || setweight(to_tsvector('english', left(coalesce(NEW.extracted_text,''), 200000)), 'D');
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS files_search_vec_trg ON files;
CREATE TRIGGER files_search_vec_trg BEFORE INSERT OR UPDATE OF title, tags, description, ai_summary, original_name, extracted_text
  ON files FOR EACH ROW EXECUTE FUNCTION files_search_vec_update();

CREATE TABLE IF NOT EXISTS file_collaborators (
  id         BIGSERIAL PRIMARY KEY,
  file_id    BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  email      TEXT NOT NULL,
  user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (file_id, email)
);
CREATE INDEX IF NOT EXISTS collab_user ON file_collaborators(user_id);
CREATE INDEX IF NOT EXISTS collab_email ON file_collaborators(email);

CREATE TABLE IF NOT EXISTS file_versions (
  id            BIGSERIAL PRIMARY KEY,
  file_id       BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  version       INT NOT NULL,
  storage_key   TEXT,
  original_name TEXT NOT NULL,
  file_type     TEXT NOT NULL,
  size_bytes    BIGINT NOT NULL DEFAULT 0,
  note          TEXT NOT NULL DEFAULT '',
  created_by    BIGINT REFERENCES users(id) ON DELETE SET NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (file_id, version)
);

CREATE TABLE IF NOT EXISTS topics (
  id         BIGSERIAL PRIMARY KEY,
  slug       TEXT UNIQUE NOT NULL,
  name       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS topics_name_trgm ON topics USING gin (lower(name) gin_trgm_ops);

CREATE TABLE IF NOT EXISTS file_topics (
  file_id  BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
  source   TEXT NOT NULL DEFAULT 'auto' CHECK (source IN ('user','auto','ai')),
  weight   REAL NOT NULL DEFAULT 1,
  PRIMARY KEY (file_id, topic_id)
);
CREATE INDEX IF NOT EXISTS file_topics_topic ON file_topics(topic_id);

-- Curation by consent: org asks to feature a member's file; the uploader approves.
CREATE TABLE IF NOT EXISTS org_features (
  org_id       BIGINT NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
  file_id      BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','declined')),
  requested_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
  note         TEXT NOT NULL DEFAULT '',
  pinned       BOOLEAN NOT NULL DEFAULT false,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at   TIMESTAMPTZ,
  PRIMARY KEY (org_id, file_id)
);
CREATE INDEX IF NOT EXISTS org_features_file ON org_features(file_id);

CREATE TABLE IF NOT EXISTS comments (
  id         BIGSERIAL PRIMARY KEY,
  file_id    BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  parent_id  BIGINT REFERENCES comments(id) ON DELETE CASCADE,
  root_id    BIGINT REFERENCES comments(id) ON DELETE CASCADE,
  depth      INT NOT NULL DEFAULT 0,
  body       TEXT NOT NULL,
  like_count INT NOT NULL DEFAULT 0,
  reply_count INT NOT NULL DEFAULT 0,
  deleted    BOOLEAN NOT NULL DEFAULT false,
  hidden     BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  edited_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS comments_file ON comments(file_id, created_at);
CREATE INDEX IF NOT EXISTS comments_root ON comments(root_id, created_at);

CREATE TABLE IF NOT EXISTS comment_likes (
  comment_id BIGINT NOT NULL REFERENCES comments(id) ON DELETE CASCADE,
  user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (comment_id, user_id)
);

CREATE TABLE IF NOT EXISTS likes (
  file_id    BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (file_id, user_id)
);
CREATE INDEX IF NOT EXISTS likes_user ON likes(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS likes_time ON likes(created_at);

CREATE TABLE IF NOT EXISTS saves (
  file_id    BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (file_id, user_id)
);
CREATE INDEX IF NOT EXISTS saves_user ON saves(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS follows (
  follower_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  user_id     BIGINT REFERENCES users(id) ON DELETE CASCADE,
  org_id      BIGINT REFERENCES orgs(id) ON DELETE CASCADE,
  topic_id    BIGINT REFERENCES topics(id) ON DELETE CASCADE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(user_id, org_id, topic_id) = 1)
);
CREATE UNIQUE INDEX IF NOT EXISTS follows_user_uq ON follows(follower_id, user_id) WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS follows_org_uq ON follows(follower_id, org_id) WHERE org_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS follows_topic_uq ON follows(follower_id, topic_id) WHERE topic_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS follows_target_user ON follows(user_id);
CREATE INDEX IF NOT EXISTS follows_target_org ON follows(org_id);

CREATE TABLE IF NOT EXISTS notifications (
  id         BIGSERIAL PRIMARY KEY,
  user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL,
  actor_id   BIGINT REFERENCES users(id) ON DELETE CASCADE,
  file_id    BIGINT REFERENCES files(id) ON DELETE CASCADE,
  org_id     BIGINT REFERENCES orgs(id) ON DELETE CASCADE,
  comment_id BIGINT REFERENCES comments(id) ON DELETE CASCADE,
  data       JSONB NOT NULL DEFAULT '{}',
  read_at    TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notifications_user ON notifications(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS notifications_unread ON notifications(user_id) WHERE read_at IS NULL;

-- Engagement events. view/download/share are deduped per visitor per day (unique index).
CREATE TABLE IF NOT EXISTS events (
  id         BIGSERIAL PRIMARY KEY,
  file_id    BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('view','download','share')),
  user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
  visitor    TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT 'direct',
  day        DATE NOT NULL DEFAULT current_date,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS events_dedupe ON events(file_id, kind, day, visitor);
CREATE INDEX IF NOT EXISTS events_time ON events(created_at);
CREATE INDEX IF NOT EXISTS events_file_day ON events(file_id, day);

CREATE TABLE IF NOT EXISTS collections (
  id          BIGSERIAL PRIMARY KEY,
  user_id     BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  org_id      BIGINT REFERENCES orgs(id) ON DELETE CASCADE,
  title       TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  visibility  TEXT NOT NULL DEFAULT 'public' CHECK (visibility IN ('public','org','private','link')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS collections_user ON collections(user_id);
CREATE INDEX IF NOT EXISTS collections_org ON collections(org_id);

CREATE TABLE IF NOT EXISTS collection_items (
  collection_id BIGINT NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  file_id       BIGINT NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  position      INT NOT NULL DEFAULT 0,
  added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (collection_id, file_id)
);

CREATE TABLE IF NOT EXISTS reports (
  id          BIGSERIAL PRIMARY KEY,
  file_id     BIGINT REFERENCES files(id) ON DELETE CASCADE,
  comment_id  BIGINT REFERENCES comments(id) ON DELETE CASCADE,
  reporter_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
  reason      TEXT NOT NULL,
  details     TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','dismissed','actioned')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_by BIGINT REFERENCES users(id) ON DELETE SET NULL,
  resolved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS reports_status ON reports(status, created_at DESC);
