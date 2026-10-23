"""File storage. Local VPS disk by default; any S3-compatible bucket when STORAGE_BACKEND=s3."""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Optional

from app import config

LOCAL_ROOT = Path(config.DATA_DIR) / "storage"
CACHE_ROOT = Path(config.CACHE_DIR)


class LocalStorage:
    kind = "local"

    def __init__(self):
        LOCAL_ROOT.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (LOCAL_ROOT / key).resolve()
        if not str(p).startswith(str(LOCAL_ROOT.resolve())):
            raise ValueError("bad key")
        return p

    def put_file(self, key: str, src: Path, content_type: str = "application/octet-stream"):
        dest = self._p(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream"):
        dest = self._p(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def local_path(self, key: str) -> Optional[Path]:
        p = self._p(key)
        return p if p.exists() else None

    def exists(self, key: str) -> bool:
        return self._p(key).exists()

    def delete(self, key: str):
        try:
            self._p(key).unlink(missing_ok=True)
        except Exception:
            pass

    def public_url(self, key: str) -> Optional[str]:
        return None  # served through the app


class S3Storage:
    kind = "s3"

    def __init__(self):
        import boto3
        from botocore.config import Config
        self.bucket = config.S3_BUCKET
        self.client = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT_URL or None,
            region_name=config.S3_REGION or None,
            aws_access_key_id=config.S3_ACCESS_KEY_ID or None,
            aws_secret_access_key=config.S3_SECRET_ACCESS_KEY or None,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)

    def put_file(self, key, src: Path, content_type="application/octet-stream"):
        self.client.upload_file(str(src), self.bucket, key, ExtraArgs={"ContentType": content_type})

    def put_bytes(self, key, data: bytes, content_type="application/octet-stream"):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def local_path(self, key: str) -> Optional[Path]:
        """Download into a small local cache (needed for previews / zips)."""
        name = hashlib.sha1(key.encode()).hexdigest() + Path(key).suffix
        p = CACHE_ROOT / name
        if p.exists():
            return p
        try:
            self.client.download_file(self.bucket, key, str(p))
            return p
        except Exception:
            return None

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def delete(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:
            pass

    def presigned(self, key: str, filename: str, inline: bool = False, expires: int = 600) -> str:
        disp = f'{"inline" if inline else "attachment"}; filename="{filename}"'
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key, "ResponseContentDisposition": disp},
            ExpiresIn=expires)

    def public_url(self, key: str) -> Optional[str]:
        if config.S3_PUBLIC_BASE_URL:
            return config.S3_PUBLIC_BASE_URL.rstrip("/") + "/" + key
        return None


_store = None


def store():
    global _store
    if _store is None:
        _store = S3Storage() if config.STORAGE_BACKEND == "s3" else LocalStorage()
    return _store
