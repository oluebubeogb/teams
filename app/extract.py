"""Text extraction, lightweight keyword/topic extraction and first-page thumbnails."""
from __future__ import annotations

import io
import logging
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Optional

log = logging.getLogger("teams.extract")
MAX_TEXT = 300_000

OFFICE_TYPES = {"pptx", "docx", "xlsx", "xls"}
IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "gif"}
TEXT_TYPES = {"txt", "md", "csv"}


def extract_text(path: Path, file_type: str) -> tuple[str, Optional[int]]:
    """Return (text, page_count)."""
    text, pages = "", None
    try:
        if file_type == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            pages = len(reader.pages)
            parts = []
            for page in reader.pages[:400]:
                t = page.extract_text() or ""
                parts.append(t)
                if sum(len(p) for p in parts) > MAX_TEXT:
                    break
            text = "\n".join(parts)
        elif file_type == "docx":
            from docx import Document
            doc = Document(str(path))
            parts = [p.text for p in doc.paragraphs]
            for t in doc.tables:
                for row in t.rows:
                    parts.append(" ".join(c.text for c in row.cells))
            text = "\n".join(parts)
        elif file_type in ("xlsx", "xls"):
            from openpyxl import load_workbook
            wb = load_workbook(str(path), read_only=True, data_only=True)
            parts = []
            for sheet in wb.worksheets:
                parts.append(sheet.title)
                for row in sheet.iter_rows(values_only=True):
                    parts.append(" ".join(str(c) for c in row if c is not None))
                    if len(parts) > 20000:
                        break
            wb.close()
            text = "\n".join(parts)
        elif file_type == "pptx":
            from pptx import Presentation
            prs = Presentation(str(path))
            pages = len(prs.slides)
            parts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False) and shape.text_frame:
                        parts.append(shape.text_frame.text)
                if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
                    parts.append(slide.notes_slide.notes_text_frame.text)
            text = "\n".join(parts)
        elif file_type in TEXT_TYPES:
            raw = path.read_bytes()[: MAX_TEXT * 2]
            text = raw.decode("utf-8", errors="ignore")
            if file_type == "md":
                text = re.sub(r"[#>*_`~\[\]()!-]{1,}", " ", text)
    except Exception as e:  # never fail an upload because of extraction
        log.warning("extract failed for %s: %s", path, e)
    text = re.sub(r"[ \t\r\f\v]+", " ", text.replace("\x00", ""))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:MAX_TEXT].strip(), pages


# ---------- keywords ----------
STOP = set("""
a about above after again against all also am an and any are aren as at be because been before being below
between both but by can cannot could couldn did didn do does doesn doing don down during each few for from
further had hadn has hasn have haven having he her here hers herself him himself his how i if in into is isn
it its itself just let me more most must my myself no nor not now of off on once only or other ought our ours
ourselves out over own same shall she should so some such than that the their theirs them themselves then
there these they this those through to too under until up very was wasn we were weren what when where which
while who whom why will with won would you your yours yourself yourselves one two three four five six seven
eight nine ten also may might us use used using page pages new get got make made many much well within
without per via etc e.g i.e like will shall can could www http https com org net html pdf docx pptx xlsx
slide slides chapter section table figure fig yes no et al vs inc ltd total date name number amount item
note notes see show shown first second third last next however therefore thus hence every another
""".split())

WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-']{2,24}")


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s[:60]


def keywords(title: str, description: str, text: str, limit: int = 6) -> list[str]:
    """Cheap RAKE-ish keywords: frequent unigrams + bigrams, title words boosted."""
    def toks(s):
        return [w.lower().strip("-'") for w in WORD_RE.findall(s or "")]

    body = toks(text[:60000])
    head = toks(f"{title} {description}")
    uni = Counter()
    bi = Counter()
    for seq, w in ((body, 1.0), (head, 4.0)):
        prev = None
        for t in seq:
            if t in STOP or len(t) < 4:
                prev = None
                continue
            uni[t] += w
            if prev:
                bi[f"{prev} {t}"] += w
            prev = t
    scored = []
    for k, v in bi.items():
        if v >= 3:
            scored.append((v * 1.6, k))
    for k, v in uni.items():
        if v >= 2:
            scored.append((v, k))
    scored.sort(reverse=True)
    out, seen = [], set()
    for _, k in scored:
        parts = k.split()
        if any(p in seen for p in parts) and len(parts) == 1:
            continue
        out.append(k)
        seen.update(parts)
        if len(out) >= limit:
            break
    return out


def snippet(text: str, n: int = 260) -> str:
    t = re.sub(r"\s+", " ", text or "").strip()
    return t[:n]


# ---------- thumbnails ----------
def _webp(img, width: int = 480) -> bytes:
    from PIL import Image
    img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
    if img.width > width:
        h = int(img.height * width / img.width)
        img = img.resize((width, h), Image.LANCZOS)
    # crop very tall pages to a pleasant card ratio (≈ 4:5 .. 3:4)
    max_h = int(img.width * 1.3)
    if img.height > max_h:
        img = img.crop((0, 0, img.width, max_h))
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=72, method=4)
    return buf.getvalue()


def pdf_first_page_png(pdf: Path, out_dir: Path) -> Optional[Path]:
    base = out_dir / "thumb"
    try:
        subprocess.run(["pdftoppm", "-f", "1", "-l", "1", "-png", "-scale-to", "720", "-singlefile", str(pdf), str(base)],
                       capture_output=True, timeout=60, check=False)
    except Exception as e:
        log.warning("pdftoppm failed: %s", e)
        return None
    p = base.with_suffix(".png")
    return p if p.exists() else None


def office_to_pdf(src: Path, out_dir: Path) -> Optional[Path]:
    profile = out_dir / "lo-profile"
    profile.mkdir(exist_ok=True)
    try:
        r = subprocess.run(["soffice", "--headless", "--norestore", f"-env:UserInstallation={profile.as_uri()}",
                            "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
                           capture_output=True, timeout=180, check=False)
    except FileNotFoundError:
        try:
            r = subprocess.run(["libreoffice", "--headless", f"-env:UserInstallation={profile.as_uri()}",
                                "--convert-to", "pdf", "--outdir", str(out_dir), str(src)],
                               capture_output=True, timeout=180, check=False)
        except Exception:
            return None
    except Exception as e:
        log.warning("office convert failed: %s", e)
        return None
    p = out_dir / (src.stem + ".pdf")
    return p if p.exists() and p.stat().st_size > 0 else None


def make_thumbnail(path: Path, file_type: str) -> tuple[Optional[bytes], Optional[Path]]:
    """Return (webp bytes, office->pdf path if produced). Caller must use pdf before tmp dir cleanup."""
    from PIL import Image
    if file_type in IMAGE_TYPES:
        try:
            with Image.open(path) as im:
                im.seek(0)
                return _webp(im.copy()), None
        except Exception:
            return None, None
    with tempfile.TemporaryDirectory(prefix="teams-thumb-") as tmp:
        tmpd = Path(tmp)
        pdf_bytes = None
        pdf = None
        if file_type == "pdf":
            pdf = path
        elif file_type in OFFICE_TYPES:
            src = tmpd / f"src.{file_type}"
            src.write_bytes(path.read_bytes())
            pdf = office_to_pdf(src, tmpd)
            if pdf:
                pdf_bytes = pdf.read_bytes()
        if not pdf:
            return None, None
        png = pdf_first_page_png(pdf, tmpd)
        if not png:
            return None, None
        with Image.open(png) as im:
            data = _webp(im.copy())
        if pdf_bytes:
            keep = Path(tempfile.mkstemp(suffix=".pdf", prefix="teams-preview-")[1])
            keep.write_bytes(pdf_bytes)
            return data, keep
        return data, None


def resize_square(data: bytes, size: int = 256) -> bytes:
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.fit(im, (size, size), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=80)
        return buf.getvalue()


def resize_cover(data: bytes, width: int = 1500, height: int = 420) -> bytes:
    from PIL import Image, ImageOps
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        im = ImageOps.fit(im, (width, height), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "WEBP", quality=74)
        return buf.getvalue()
