import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "teams-super-secret-key-change-in-production-2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "app/uploads")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./teams.db")
MAX_DESCRIPTION_LENGTH = 50
MIN_PASSWORD_LENGTH = 6
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".xls"}
