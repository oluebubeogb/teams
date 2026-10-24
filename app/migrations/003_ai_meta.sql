-- AI-written titles/descriptions (reversible) + document-metadata support. Re-runnable.
ALTER TABLE files ADD COLUMN IF NOT EXISTS ai_meta BOOLEAN NOT NULL DEFAULT false;
-- NULL = not processed yet (backfill candidate) | pending | running | done | skipped | failed | off (uploader opted out) | edited
ALTER TABLE files ADD COLUMN IF NOT EXISTS ai_meta_status TEXT;
ALTER TABLE files ADD COLUMN IF NOT EXISTS ai_meta_attempts INT NOT NULL DEFAULT 0;
ALTER TABLE files ADD COLUMN IF NOT EXISTS ai_meta_at TIMESTAMPTZ;
ALTER TABLE files ADD COLUMN IF NOT EXISTS original_title TEXT;
ALTER TABLE files ADD COLUMN IF NOT EXISTS original_description TEXT;
CREATE INDEX IF NOT EXISTS files_ai_meta_todo ON files (id) WHERE ai_meta_status IS NULL OR ai_meta_status IN ('failed', 'pending', 'running');
