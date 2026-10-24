-- Sharp, full-page thumbnails with their real proportions (cards reserve the right space, no layout shift). Re-runnable.
ALTER TABLE files ADD COLUMN IF NOT EXISTS thumb_w INT;
ALTER TABLE files ADD COLUMN IF NOT EXISTS thumb_h INT;
-- 1 = legacy 480px cropped thumbnail; 2 = HD full first page + small variant. Older revs are regenerated in the background.
ALTER TABLE files ADD COLUMN IF NOT EXISTS thumb_rev SMALLINT NOT NULL DEFAULT 1;
CREATE INDEX IF NOT EXISTS files_thumb_todo ON files (id) WHERE thumb_key IS NOT NULL AND thumb_rev < 2;
