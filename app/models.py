from pydantic import BaseModel, Field, field_validator
from typing import Optional
import re


class UserCreate(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    email: str = Field(..., min_length=5)
    phone: str = Field(..., min_length=7, max_length=20)
    password: str = Field(..., min_length=6)
    username: str = Field(..., min_length=3, max_length=64)

    @field_validator("email")
    @classmethod
    def email_lower(cls, v):
        return v.lower().strip()

    @field_validator("phone")
    @classmethod
    def phone_clean(cls, v):
        return v.strip().replace(" ", "").replace("-", "")

    @field_validator("username")
    @classmethod
    def username_format(cls, v: str) -> str:
        """Match Collab Accounts rules: 3–63 chars, start with letter/number, only a-z 0-9 _ -"""
        v = v.strip().lower()
        if not re.match(r"^[a-z0-9][a-z0-9_-]{2,62}$", v):
            raise ValueError(
                "Username must be 3–63 chars, start with letter/number, only a-z 0-9 _ -"
            )
        return v


class UserLogin(BaseModel):
    identifier: str  # email or phone
    password: str


class ForgotPassword(BaseModel):
    email: str
    phone: str


class ResetPassword(BaseModel):
    email: str
    phone: str
    new_password: str = Field(..., min_length=6)


class ProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    bio: Optional[str] = Field(None, max_length=300)


class FileUploadMeta(BaseModel):
    title: str = Field(..., max_length=300)
    description: str = Field(..., max_length=300)
    url: Optional[str] = None
    collaborators: Optional[str] = None  # comma-separated emails


class FileEditMeta(BaseModel):
    title: Optional[str] = Field(None, max_length=300)
    description: Optional[str] = Field(None, max_length=300)
    collaborators: Optional[str] = None  # comma-separated emails (replaces list)


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str
    email: str
    phone: str
    bio: str
    created_at: str


class FileOut(BaseModel):
    id: int
    filename: str
    original_name: str
    file_type: str
    title: str
    description: str
    url: Optional[str]
    created_at: str
    view_count: int
    download_count: int
    author_name: str
    author_username: str
    author_initials: str
    relative_date: str
