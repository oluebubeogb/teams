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

# Optional AI (OpenAI-compatible). Disabled when no primary/secondary base URL is set.
AI_PRIMARY_BASE_URL = env("AI_PRIMARY_BASE_URL").rstrip("/")
AI_PRIMARY_API_KEY = env("AI_PRIMARY_API_KEY")
AI_PRIMARY_MODEL = env("AI_PRIMARY_MODEL")
AI_SECONDARY_BASE_URL = env("AI_SECONDARY_BASE_URL").rstrip("/")
AI_SECONDARY_API_KEY = env("AI_SECONDARY_API_KEY")
AI_SECONDARY_MODEL = env("AI_SECONDARY_MODEL")
AI_TIMEOUT_SECONDS = env_int("AI_TIMEOUT_SECONDS", 45)
AI_AUTO_TAG = env_bool("AI_AUTO_TAG", True)

# Moderation / badges
ADMIN_USERNAMES = env_list("ADMIN_USERNAMES")      # site admins (moderation, verify orgs)
VERIFIED_ORGS = env_list("VERIFIED_ORGS")          # org slugs always shown as verified
REPORT_AUTOHIDE_THRESHOLD = env_int("REPORT_AUTOHIDE_THRESHOLD", 5)

# Collab Suite service-to-service sync (Crede <-> Teams). Same value on both apps; blank disables /api/suite/*.
SUITE_SYNC_TOKEN = env("SUITE_SYNC_TOKEN")
