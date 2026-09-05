import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "change-this-to-a-long-random-string")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))

ACCOUNTS_URL = os.getenv("ACCOUNTS_URL", "https://accounts.collab.name.ng").rstrip("/")
USE_ACCOUNTS = os.getenv("USE_ACCOUNTS", "true").lower() in ("1", "true", "yes")

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "app/uploads")
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./teams.db")
MAX_DESCRIPTION_LENGTH = 300
MAX_TITLE_LENGTH = 300
MIN_PASSWORD_LENGTH = 6
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".xls"}

COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN", "")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() in ("1", "true", "yes")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "lax")
