-- Suite org sync (Crede <-> Teams). Idempotent.
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS source      TEXT NOT NULL DEFAULT 'teams';
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS external_id TEXT;
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS org_type    TEXT NOT NULL DEFAULT 'company';
ALTER TABLE orgs ADD COLUMN IF NOT EXISTS updated_at  TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE UNIQUE INDEX IF NOT EXISTS orgs_source_external ON orgs(source, external_id) WHERE external_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS orgs_updated_at ON orgs(updated_at);

CREATE OR REPLACE FUNCTION teams_touch_org() RETURNS trigger AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS orgs_touch ON orgs;
CREATE TRIGGER orgs_touch BEFORE UPDATE ON orgs FOR EACH ROW
  WHEN (OLD.* IS DISTINCT FROM NEW.*) EXECUTE FUNCTION teams_touch_org();

-- Membership changes bump the org so "since" pulls pick them up.
CREATE OR REPLACE FUNCTION teams_touch_org_from_member() RETURNS trigger AS $$
BEGIN
  UPDATE orgs SET updated_at = now() WHERE id = COALESCE(NEW.org_id, OLD.org_id);
  RETURN NULL;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS org_members_touch ON org_members;
CREATE TRIGGER org_members_touch AFTER INSERT OR UPDATE OR DELETE ON org_members
  FOR EACH ROW EXECUTE FUNCTION teams_touch_org_from_member();
