"""PDF operations: blank detect, reorder, rotate, delete pages, compress."""
from __future__ import annotations
import io
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF
import img2pdf
import numpy as np
from PIL import Image
from pypdf import PdfReader, PdfWriter

from .config import settings

ALLOWED_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


# ---------- Blank detection ----------

def detect_blank_pages(pdf_bytes: bytes) -> list[dict]:
    """Return per-page info: {index, blank, reason}."""
    out = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            if len(text) >= settings.blank_text_threshold:
                out.append({"index": i, "blank": False, "reason": "text"})
                continue
            pix = page.get_pixmap(dpi=settings.render_dpi, colorspace=fitz.csGRAY)
            arr = np.frombuffer(pix.samples, dtype=np.uint8)
            std = float(arr.std())
            blank = std < settings.blank_pixel_std_threshold
            out.append({
                "index": i,
                "blank": blank,
                "reason": f"pixel_std={std:.2f}",
            })
    finally:
        doc.close()
    return out


# ---------- Page edits ----------

def apply_page_ops(
    pdf_bytes: bytes,
    keep_order: list[int] | None = None,
    rotations: dict[int, int] | None = None,
    delete: Iterable[int] | None = None,
) -> bytes:
    """
    keep_order: source page indices in new order. If None, all pages original order.
    rotations: {source_index: degrees} (0/90/180/270), applied before reorder.
    delete: source indices to drop (applied if keep_order None).
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    n = len(reader.pages)
    rotations = rotations or {}

    if keep_order is None:
        drop = set(delete or [])
        order = [i for i in range(n) if i not in drop]
    else:
        order = list(keep_order)

    writer = PdfWriter()
    for src in order:
        if src < 0 or src >= n:
            raise ValueError(f"page index out of range: {src}")
        page = reader.pages[src]
        rot = rotations.get(src, 0) % 360
        if rot:
            page.rotate(rot)
        writer.add_page(page)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------- Compression ----------

# Presets tuned for aggressiveness. Key differences:
# - image_dpi: target DPI for embedded raster images (down-sample above this)
# - jpeg_quality: 1-100
# - rasterize: if True, render each page to JPEG and rebuild PDF (max shrink, loses text layer)
COMPRESS_PRESETS: dict[str, dict] = {
    "low":       {"gs": "/printer",  "image_dpi": 200, "jpeg_q": 85, "rasterize": False},
    "medium":    {"gs": "/ebook",    "image_dpi": 150, "jpeg_q": 75, "rasterize": False},
    "high":      {"gs": "/ebook",    "image_dpi": 100, "jpeg_q": 60, "rasterize": False},
    "extreme":   {"gs": "/screen",   "image_dpi": 72,  "jpeg_q": 45, "rasterize": False},
    "rasterize": {"gs": "/screen",   "image_dpi": 100, "jpeg_q": 55, "rasterize": True},
}


def _gs_compress(pdf_bytes: bytes, preset: dict) -> bytes:
    """Run Ghostscript with fine-grained image downsampling."""
    dpi = preset["image_dpi"]
    q = preset["jpeg_q"]
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "in.pdf"
        dst = Path(td) / "out.pdf"
        src.write_bytes(pdf_bytes)
        cmd = [
            settings.ghostscript_bin,
            "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.5",
            f"-dPDFSETTINGS={preset['gs']}",
            "-dNOPAUSE", "-dQUIET", "-dBATCH",
            "-dDetectDuplicateImages=true",
            "-dCompressFonts=true",
            "-dSubsetFonts=true",
            # force image downsampling + JPEG re-encode
            "-dColorImageDownsampleType=/Bicubic",
            f"-dColorImageResolution={dpi}",
            "-dDownsampleColorImages=true",
            "-dGrayImageDownsampleType=/Bicubic",
            f"-dGrayImageResolution={dpi}",
            "-dDownsampleGrayImages=true",
            "-dMonoImageDownsampleType=/Bicubic",
            f"-dMonoImageResolution={max(dpi, 300)}",
            "-dDownsampleMonoImages=true",
            "-dAutoFilterColorImages=false",
            "-dColorImageFilter=/DCTEncode",
            "-dAutoFilterGrayImages=false",
            "-dGrayImageFilter=/DCTEncode",
            # JPEG quality via dict (gs-specific hack)
            f"-c", f".setpdfwrite << /JPEGQ {q} >> setdistillerparams",
            "-f", str(src),
            f"-sOutputFile={dst}",
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return dst.read_bytes()


def _rasterize_compress(pdf_bytes: bytes, preset: dict) -> bytes:
    """Render each page → JPEG → rebuild PDF. Max shrink, kills text layer."""
    dpi = preset["image_dpi"]
    q = preset["jpeg_q"]
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        images: list[bytes] = []
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=q, optimize=True)
            images.append(buf.getvalue())
    finally:
        doc.close()
    return img2pdf.convert(images)


def compress_with_ghostscript(pdf_bytes: bytes, quality: str | None = None) -> bytes:
    """
    Compress PDF. `quality` accepts preset name (low/medium/high/extreme/rasterize)
    or raw Ghostscript setting (/screen, /ebook, /printer, /prepress).
    Returns compressed bytes; falls back to source if compression bigger.
    """
    q = quality or "medium"
    # raw gs setting fallback
    if q.startswith("/"):
        preset = {"gs": q, "image_dpi": 150, "jpeg_q": 75, "rasterize": False}
    else:
        preset = COMPRESS_PRESETS.get(q, COMPRESS_PRESETS["medium"])

    try:
        if preset["rasterize"]:
            out = _rasterize_compress(pdf_bytes, preset)
        else:
            out = _gs_compress(pdf_bytes, preset)
    except subprocess.CalledProcessError:
        return pdf_bytes

    return out if len(out) < len(pdf_bytes) else pdf_bytes


def page_count(pdf_bytes: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf_bytes)).pages)


# ---------- Merge ----------

def merge_pdfs(pdfs: list[bytes]) -> bytes:
    """Concatenate multiple PDFs in given order."""
    if not pdfs:
        raise ValueError("no PDFs")
    writer = PdfWriter()
    for data in pdfs:
        reader = PdfReader(io.BytesIO(data))
        for page in reader.pages:
            writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------- Images -> PDF ----------

def images_to_pdf(images: list[tuple[str, bytes]]) -> bytes:
    """
    images: list of (filename, bytes). Order preserved.
    Auto-rotates EXIF, normalizes mode, packs into single PDF via img2pdf.
    """
    if not images:
        raise ValueError("no images")
    normalized: list[bytes] = []
    for name, data in images:
        ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ALLOWED_IMAGE_EXT:
            raise ValueError(f"unsupported image: {name}")
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception as e:
            raise ValueError(f"invalid image {name}: {e}")
        # normalize: drop alpha, EXIF transpose, ensure JPEG/PNG-friendly
        from PIL import ImageOps
        im = ImageOps.exif_transpose(im)
        if im.mode in ("RGBA", "LA", "P"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            if im.mode == "P":
                im = im.convert("RGBA")
            bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
            im = bg
        elif im.mode != "RGB" and im.mode != "L":
            im = im.convert("RGB")
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=85, optimize=True)
        normalized.append(out.getvalue())
    return img2pdf.convert(normalized)


# ---------- Export to images (per page) ----------

def pdf_to_images(pdf_bytes: bytes, fmt: str = "png", dpi: int = 150) -> list[bytes]:
    """Render each page as image bytes. fmt: png|jpg."""
    fmt = fmt.lower()
    if fmt not in ("png", "jpg", "jpeg"):
        raise ValueError("fmt must be png or jpg")
    out: list[bytes] = []
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            buf = io.BytesIO()
            if fmt.startswith("j"):
                img.save(buf, format="JPEG", quality=85, optimize=True)
            else:
                img.save(buf, format="PNG", optimize=True)
            out.append(buf.getvalue())
    finally:
        doc.close()
    return out


# ---------- Split / Extract ----------

def parse_page_ranges(spec: str, total_pages: int) -> list[int]:
    """
    Parse '1-3,5,7-9' (1-based) into sorted unique 0-based indices.
    Raises ValueError on invalid syntax or out-of-range.
    """
    if not spec or not spec.strip():
        raise ValueError("empty range spec")
    result: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise ValueError(f"invalid range: {part}")
            if start < 1 or end < 1 or start > end:
                raise ValueError(f"invalid range: {part}")
            for n in range(start, end + 1):
                if n > total_pages:
                    raise ValueError(f"page {n} out of range (total {total_pages})")
                result.add(n - 1)
        else:
            try:
                n = int(part)
            except ValueError:
                raise ValueError(f"invalid page: {part}")
            if n < 1 or n > total_pages:
                raise ValueError(f"page {n} out of range (total {total_pages})")
            result.add(n - 1)
    if not result:
        raise ValueError("no pages selected")
    return sorted(result)


def extract_pages(pdf_bytes: bytes, indices: list[int]) -> bytes:
    """Build new PDF with only the given 0-based page indices, in given order."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    n = len(reader.pages)
    writer = PdfWriter()
    for i in indices:
        if i < 0 or i >= n:
            raise ValueError(f"page index out of range: {i}")
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def split_pdf(
    pdf_bytes: bytes,
    mode: str = "single",
    every: int = 1,
    ranges_spec: str | None = None,
) -> list[bytes]:
    """
    mode:
      'single'  → each page becomes a separate PDF
      'every'   → group every N pages into one PDF
      'ranges'  → ranges_spec like '1-3,5,7-9' → ONE PDF per comma group
    Returns list of PDF bytes.
    """
    reader = PdfReader(io.BytesIO(pdf_bytes))
    n = len(reader.pages)
    if n == 0:
        raise ValueError("PDF has no pages")

    groups: list[list[int]] = []
    if mode == "single":
        groups = [[i] for i in range(n)]
    elif mode == "every":
        if every < 1:
            raise ValueError("every must be >= 1")
        for i in range(0, n, every):
            groups.append(list(range(i, min(i + every, n))))
    elif mode == "ranges":
        if not ranges_spec:
            raise ValueError("ranges_spec required for mode='ranges'")
        for part in ranges_spec.split(","):
            indices = parse_page_ranges(part, n)
            groups.append(indices)
    else:
        raise ValueError(f"unknown mode: {mode}")

    out: list[bytes] = []
    for grp in groups:
        writer = PdfWriter()
        for i in grp:
            writer.add_page(reader.pages[i])
        buf = io.BytesIO()
        writer.write(buf)
        out.append(buf.getvalue())
    return out


# ---------- Redact ----------

def redact_areas(pdf_bytes: bytes, areas: list[dict]) -> bytes:
    """
    Permanently remove content under rectangular areas, fill with black.
    `areas`: list of dicts with NORMALIZED (0-1) coords:
        {page: int (0-based), x: float, y: float, w: float, h: float}
    Coordinates are fraction of page width/height, origin top-left.
    """
    if not areas:
        raise ValueError("no areas")
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        # group by page
        by_page: dict[int, list[dict]] = {}
        for a in areas:
            p = int(a["page"])
            if p < 0 or p >= len(doc):
                raise ValueError(f"page index out of range: {p}")
            by_page.setdefault(p, []).append(a)

        for p, items in by_page.items():
            page = doc[p]
            pw, ph = page.rect.width, page.rect.height
            for a in items:
                x = float(a["x"]) * pw
                y = float(a["y"]) * ph
                w = float(a["w"]) * pw
                h = float(a["h"]) * ph
                if w <= 0 or h <= 0:
                    continue
                rect = fitz.Rect(x, y, x + w, y + h)
                page.add_redact_annot(rect, fill=(0, 0, 0))
            page.apply_redactions()
        out = io.BytesIO()
        doc.save(out, garbage=4, deflate=True)
        return out.getvalue()
    finally:
        doc.close()


# ---------- Password protect / unlock ----------

def protect_pdf(pdf_bytes: bytes, password: str) -> bytes:
    """Encrypt PDF with AES-128. Same password for user + owner (simple model)."""
    if not password:
        raise ValueError("password required")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        raise ValueError("PDF is already encrypted — unlock first")
    writer = PdfWriter()
    writer.append_pages_from_reader(reader)
    writer.encrypt(user_password=password, owner_password=password, use_128bit=True)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def unlock_pdf(pdf_bytes: bytes, password: str) -> bytes:
    """Remove password from PDF. Raises ValueError on wrong password."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if not reader.is_encrypted:
        return pdf_bytes  # nothing to do
    try:
        ok = reader.decrypt(password or "")
    except Exception as e:
        raise ValueError(f"decrypt failed: {e}")
    # pypdf returns 0 on failure, 1 (user) or 2 (owner) on success
    if not ok:
        raise ValueError("wrong password")
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def is_encrypted(pdf_bytes: bytes) -> bool:
    try:
        return PdfReader(io.BytesIO(pdf_bytes)).is_encrypted
    except Exception:
        return False


def safe_page_count(pdf_bytes: bytes) -> int:
    """page_count that returns 0 for encrypted/broken PDFs instead of raising."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        if reader.is_encrypted:
            return 0
        return len(reader.pages)
    except Exception:
        return 0
