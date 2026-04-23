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
