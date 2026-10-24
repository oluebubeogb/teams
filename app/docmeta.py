"""Document properties from Teams data.

Title = the title shown on Teams, Author = the sharer's name, Subject = main tag (or top topic),
Keywords = all tags, Creator/Producer = Collab Teams. Stamped at serve time into a small on-disk cache
(keyed by a hash of the metadata), so edits to title/description/tags show up on the next view/download and
older files/previews are covered without any backfill. Stored originals are never modified.
  - PDF (originals and LibreOffice previews): pypdf incremental update (original bytes + a tiny appended trailer).
  - DOCX/PPTX/XLSX: docProps/core.xml rewritten in a copied zip.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape

from app import config
from app.db import q

log = logging.getLogger("teams.docmeta")
CACHE = Path(config.CACHE_DIR) / "meta"
OOXML = {"docx", "pptx", "xlsx"}
STAMPABLE = OOXML | {"pdf"}
PRODUCER = "Collab Teams"
STAMP_VERSION = 2  # bump to restamp every cached copy


def file_meta(f: dict) -> dict:
    """f is a FILE_SELECT row (title, description, tags, a_name, a_username, created_at, updated_at, o_name)."""
    tags = [t for t in (f.get("tags") or []) if t]
    topics = []
    if not tags:
        topics = [r["name"] for r in q("""SELECT t.name FROM file_topics ft JOIN topics t ON t.id=ft.topic_id
                                           WHERE ft.file_id=%s ORDER BY ft.weight DESC, t.name LIMIT 6""", (f["id"],))]
    words = tags or topics

    def nice(t: str) -> str:
        return t[:1].upper() + t[1:] if t and t.islower() else t

    title = (f.get("title") or "").strip() or Path(f.get("original_name") or "Untitled").stem
    author = (f.get("a_name") or f.get("a_username") or "").strip()
    if not author and f.get("o_name"):
        author = f["o_name"]
    return {
        "title": title[:300], "author": author[:200], "subject": nice(words[0]) if words else "",
        "keywords": ", ".join(words), "description": (f.get("description") or "").strip()[:2000],
        "created": f.get("created_at"), "modified": f.get("updated_at") or f.get("created_at"),
    }


def _hash(meta: dict, extra: str) -> str:
    raw = json.dumps({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in meta.items()}, sort_keys=True) + extra
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _pdf_date(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("D:%Y%m%d%H%M%SZ")


def stamp_pdf(src: Path, dst: Path, meta: dict, own_dates: bool = False) -> bool:
    """own_dates: our own rendition (preview) -> dates come from Teams, not from the LibreOffice conversion."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import NameObject
    info = {"/Title": meta["title"], "/Author": meta["author"], "/Subject": meta["subject"] or meta["title"],
            "/Keywords": meta["keywords"], "/Creator": PRODUCER, "/Producer": PRODUCER}
    if _pdf_date(meta.get("modified")):
        info["/ModDate"] = _pdf_date(meta["modified"])
    try:
        if PdfReader(str(src)).is_encrypted:
            return False
    except Exception as e:
        log.warning("pdf unreadable %s: %s", src, e)
        return False
    for incremental in (True, False):
        try:
            w = PdfWriter(str(src), incremental=True) if incremental else PdfWriter(clone_from=str(src))
            if (own_dates or "/CreationDate" not in (w.metadata or {})) and _pdf_date(meta.get("created")):
                info["/CreationDate"] = _pdf_date(meta["created"])
            w.add_metadata(info)
            root = w._root_object
            if NameObject("/Metadata") in root:   # XMP would override the Info dict in most viewers (pdf.js, Acrobat)
                del root[NameObject("/Metadata")]
            tmp = dst.with_suffix(".part")
            with open(tmp, "wb") as fh:
                w.write(fh)
            os.replace(tmp, dst)
            return True
        except Exception as e:
            log.warning("pdf stamp (%s) failed for %s: %s", "incremental" if incremental else "rewrite", src, e)
    return False


def _w3c(dt: Optional[datetime]) -> str:
    dt = (dt or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def core_xml(meta: dict) -> bytes:
    e = lambda s: escape(s or "")  # noqa: E731
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{e(meta["title"])}</dc:title><dc:subject>{e(meta["subject"])}</dc:subject>'
            f'<dc:creator>{e(meta["author"])}</dc:creator><cp:keywords>{e(meta["keywords"])}</cp:keywords>'
            f'<dc:description>{e(meta["description"])}</dc:description><cp:lastModifiedBy>{e(meta["author"])}</cp:lastModifiedBy>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{_w3c(meta.get("created"))}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{_w3c(meta.get("modified"))}</dcterms:modified>'
            '</cp:coreProperties>').encode("utf-8")


def _core_part(z: zipfile.ZipFile) -> str:
    try:
        rels = z.read("_rels/.rels").decode("utf-8", "replace")
        m = re.search(r'<Relationship[^>]+Type="[^"]*core-properties"[^>]*Target="/?([^"]+)"', rels) or \
            re.search(r'<Relationship[^>]+Target="/?([^"]+)"[^>]*Type="[^"]*core-properties"', rels)
        if m:
            return m.group(1)
    except KeyError:
        pass
    return "docProps/core.xml"


def stamp_ooxml(src: Path, dst: Path, meta: dict) -> bool:
    try:
        with zipfile.ZipFile(src) as zin:
            core = _core_part(zin)
            names = zin.namelist()
            if core not in names:
                return False   # no core part: leave as-is rather than editing content types/rels
            tmp = dst.with_suffix(".part")
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.filename == core:
                        zout.writestr(item, core_xml(meta))
                    else:
                        with zin.open(item) as r, zout.open(item, "w") as wr:
                            shutil.copyfileobj(r, wr, 1024 * 256)
        os.replace(tmp, dst)
        return True
    except Exception as e:
        log.warning("ooxml stamp failed for %s: %s", src, e)
        return False


def _prune():
    try:
        items = sorted((p for p in CACHE.glob("*") if p.is_file()), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in items)
        cap = max(50, config.META_CACHE_MB) * 1024 * 1024
        while items and total > cap:
            p = items.pop(0)
            total -= p.stat().st_size
            p.unlink(missing_ok=True)
    except Exception:
        pass


def stamped(f: dict, src: Path, ftype: str, slot: str) -> Path:
    """Return a path to a copy of src carrying Teams metadata (or src itself if not stampable / on failure).
    slot distinguishes renditions of one file: 'orig-v3', 'preview-v3' ..."""
    if not config.META_STAMP or ftype not in STAMPABLE or not src:
        return src
    meta = file_meta(f)
    h = _hash(meta, f"{STAMP_VERSION}|{slot}|{src.stat().st_size}|{int(src.stat().st_mtime)}")
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / f"{f['id']}-{slot}-{h}.{ftype}"
    if out.exists():
        return out
    for old in CACHE.glob(f"{f['id']}-{slot}-*"):
        old.unlink(missing_ok=True)
    ok = stamp_pdf(src, out, meta, own_dates=slot.startswith("preview")) if ftype == "pdf" else stamp_ooxml(src, out, meta)
    if not ok:
        out.unlink(missing_ok=True)
        return src
    _prune()
    return out
