import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

def generate_username(full_name: str, existing_check) -> str:
    """Generate unique username from full name."""
    base = re.sub(r'[^a-z0-9]', '', full_name.lower().replace(' ', ''))[:20] or "user"
    username = base
    counter = 1
    while existing_check(username):
        username = f"{base}{counter}"
        counter += 1
    return username

def get_initials(full_name: str) -> str:
    parts = full_name.strip().split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return full_name[:2].upper() if full_name else "U"

def relative_date(dt_str: str) -> str:
    try:
        if isinstance(dt_str, str):
            dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        else:
            dt = dt_str
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.utcnow()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
        delta = now - dt
        days = delta.days
        if days < 0:
            return "just now"
        if days == 0:
            hours = delta.seconds // 3600
            if hours == 0:
                mins = delta.seconds // 60
                return "just now" if mins < 2 else f"{mins} min ago"
            return f"{hours}h ago"
        if days < 30:
            return f"{days} day{'s' if days > 1 else ''} ago"
        return dt.strftime("%d/%m/%y")
    except Exception:
        return dt_str[:10] if dt_str else ""

def extract_text_from_file(file_path: Path, file_type: str) -> str:
    """Extract searchable text from supported files."""
    text = ""
    try:
        if file_type == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(file_path))
            for page in reader.pages:
                t = page.extract_text()
                if t:
                    text += t + " "
        elif file_type == "docx":
            from docx import Document
            doc = Document(str(file_path))
            text = " ".join([p.text for p in doc.paragraphs])
        elif file_type in ("xlsx", "xls"):
            from openpyxl import load_workbook
            wb = load_workbook(str(file_path), read_only=True, data_only=True)
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    text += " ".join([str(c) for c in row if c is not None]) + " "
            wb.close()
        elif file_type == "csv":
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        elif file_type == "pptx":
            from pptx import Presentation
            prs = Presentation(str(file_path))
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        text += shape.text + " "
    except Exception as e:
        print(f"Extract error for {file_path}: {e}")
    return text[:50000]  # limit size

def score_relevance(query: str, text: str) -> int:
    """Simple keyword score: count of query terms in text."""
    if not query or not text:
        return 0
    terms = [t.lower() for t in re.findall(r'\w+', query) if len(t) > 1]
    text_lower = text.lower()
    score = 0
    for term in terms:
        score += text_lower.count(term) * (2 if len(term) > 3 else 1)
    return score
