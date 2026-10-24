"""Teams configuration. Every setting comes from the environment (see .env.example)."""
import os
from dotenv import load_dotenv

load_dotenv()


def env(key: str, default: str = "") -> str:
    v = os.getenv(key)
    return default if v is None or v.strip() == "" else v.strip()


def env_bool(key: str, default: bool = False) -> bool:
    return env(key, "true" if default else "false").lower() in ("1", "true", "yes", "on")


def env_int(key: str, default: int) -> int:
    try:
        return int(env(key, str(default)))
    except ValueError:
        return default


def env_list(key: str) -> list[str]:
    return [x.strip().lower() for x in env(key).split(",") if x.strip()]


APP_NAME = env("APP_NAME", "Teams")
APP_URL = env("APP_URL", "https://teams.collab.name.ng").rstrip("/")
SECRET_KEY = env("SECRET_KEY", "change-this-to-a-long-random-string")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = env_int("ACCESS_TOKEN_EXPIRE_MINUTES", 10080)

# Auth: "accounts" (Collab Accounts SSO, production) or "local" (demo/dev: email-only sign-in)
AUTH_MODE = env("AUTH_MODE", "accounts" if env_bool("USE_ACCOUNTS", True) else "local").lower()
USE_ACCOUNTS = AUTH_MODE == "accounts"
ACCOUNTS_URL = env("ACCOUNTS_URL", "https://accounts.collab.name.ng").rstrip("/")
ACCOUNTS_ME_CACHE_SECONDS = env_int("ACCOUNTS_ME_CACHE_SECONDS", 60)

COOKIE_DOMAIN = env("COOKIE_DOMAIN", "")
COOKIE_SECURE = env_bool("COOKIE_SECURE", True)
COOKIE_SAMESITE = env("COOKIE_SAMESITE", "lax").lower()
CORS_ORIGINS = [o.strip() for o in env("CORS_ORIGINS", "").split(",") if o.strip()]

# Database
def _db_url() -> str:
    if env("DATABASE_URL"):
        return env("DATABASE_URL")
    from urllib.parse import quote
    return "postgresql://{}:{}@{}:{}/{}".format(quote(env("POSTGRES_USER", "teams")), quote(env("POSTGRES_PASSWORD", "teams")),
                                                env("POSTGRES_HOST", "localhost"), env("POSTGRES_PORT", "5432"), env("POSTGRES_DB", "teams"))


DATABASE_URL = _db_url()
DB_POOL_MIN = env_int("DB_POOL_MIN", 1)
DB_POOL_MAX = env_int("DB_POOL_MAX", 6)

# Storage: "local" (VPS disk under DATA_DIR) or "s3" (any S3-compatible bucket)
DATA_DIR = env("DATA_DIR", "/app/data")
STORAGE_BACKEND = env("STORAGE_BACKEND", "local").lower()
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL")
S3_REGION = env("S3_REGION", "auto")
S3_BUCKET = env("S3_BUCKET")
S3_ACCESS_KEY_ID = env("S3_ACCESS_KEY_ID")
S3_SECRET_ACCESS_KEY = env("S3_SECRET_ACCESS_KEY")
S3_PUBLIC_BASE_URL = env("S3_PUBLIC_BASE_URL")  # optional CDN/public URL for thumbnails
CACHE_DIR = env("CACHE_DIR", "/tmp/teams-cache")

MAX_UPLOAD_MB = env_int("MAX_UPLOAD_MB", 50)
MAX_TITLE_LENGTH = 300
MAX_DESCRIPTION_LENGTH = 2000
MIN_PASSWORD_LENGTH = 6
ALLOWED_EXTENSIONS = {
    ".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".txt", ".md",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
}
# Background work (thumbnails, LibreOffice). 1 keeps memory low on a small VPS.
WORKER_THREADS = env_int("WORKER_THREADS", 1)
THUMBNAILS = env_bool("THUMBNAILS", True)
THUMB_EDGE = env_int("THUMB_EDGE", 1400)              # long edge of the card image (2x a ~620px card)
THUMB_SMALL_EDGE = env_int("THUMB_SMALL_EDGE", 480)   # lists, covers, notifications
THUMB_QUALITY = env_int("THUMB_QUALITY", 82)          # WebP quality
THUMB_BACKFILL_ON_BOOT = env_bool("THUMB_BACKFILL_ON_BOOT", True)   # regenerate older thumbnails in the background
THUMB_BACKFILL_DELAY_SECONDS = env_int("THUMB_BACKFILL_DELAY_SECONDS", 2)

# Optional AI (OpenAI-compatible). Disabled when no primary/secondary base URL is set.
AI_PRIMARY_BASE_URL = env("AI_PRIMARY_BASE_URL").rstrip("/")
AI_PRIMARY_API_KEY = env("AI_PRIMARY_API_KEY")
AI_PRIMARY_MODEL = env("AI_PRIMARY_MODEL")
AI_SECONDARY_BASE_URL = env("AI_SECONDARY_BASE_URL").rstrip("/")
AI_SECONDARY_API_KEY = env("AI_SECONDARY_API_KEY")
AI_SECONDARY_MODEL = env("AI_SECONDARY_MODEL")
AI_TIMEOUT_SECONDS = env_int("AI_TIMEOUT_SECONDS", 45)
AI_AUTO_TAG = env_bool("AI_AUTO_TAG", True)
AI_MIN_INTERVAL_MS = env_int("AI_MIN_INTERVAL_MS", 1500)     # gentle spacing between AI calls (credits / rate limits)
AI_MAX_INPUT_CHARS = env_int("AI_MAX_INPUT_CHARS", 6000)     # document text sent per call
# AI titles & descriptions: default for new uploads (users can switch it off per upload session)
AI_META_DEFAULT = env_bool("AI_META_DEFAULT", True)
# Backfill older files with AI titles/descriptions in the background after boot (throttled, resumable)
AI_BACKFILL_ON_BOOT = env_bool("AI_BACKFILL_ON_BOOT", True)
AI_BACKFILL_BATCH = env_int("AI_BACKFILL_BATCH", 20)
AI_BACKFILL_DELAY_SECONDS = env_int("AI_BACKFILL_DELAY_SECONDS", 4)
AI_BACKFILL_START_DELAY_SECONDS = env_int("AI_BACKFILL_START_DELAY_SECONDS", 30)
AI_BACKFILL_MAX_PER_BOOT = env_int("AI_BACKFILL_MAX_PER_BOOT", 0)   # 0 = no cap
AI_BACKFILL_ONLY_DEFAULT_TITLES = env_bool("AI_BACKFILL_ONLY_DEFAULT_TITLES", False)

# Document metadata (Title/Author/Subject/Keywords) stamped into previews and downloads from Teams data
META_STAMP = env_bool("META_STAMP", True)
META_CACHE_MB = env_int("META_CACHE_MB", 400)                # disk cap for stamped copies under CACHE_DIR/meta

# Moderation / badges
ADMIN_USERNAMES = env_list("ADMIN_USERNAMES")      # site admins (moderation, verify orgs)
VERIFIED_ORGS = env_list("VERIFIED_ORGS")          # org slugs always shown as verified
REPORT_AUTOHIDE_THRESHOLD = env_int("REPORT_AUTOHIDE_THRESHOLD", 5)

# Collab Suite service-to-service sync (Crede <-> Teams). Same value on both apps; blank disables /api/suite/*.
SUITE_SYNC_TOKEN = env("SUITE_SYNC_TOKEN")
