import io
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Body
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from .auth import current_user
from .config import settings
from .crypto import encrypt_to_disk, decrypt_from_disk
from .db import Document, AuditLog, get_session
from .pdf_ops import (
    detect_blank_pages, apply_page_ops, compress_with_ghostscript, page_count,
    safe_page_count, merge_pdfs, images_to_pdf, pdf_to_images, ALLOWED_IMAGE_EXT,
    split_pdf, extract_pages, parse_page_ranges, redact_areas,
    protect_pdf, unlock_pdf, is_encrypted,
)

router = APIRouter(prefix="/api")


# ---------- helpers ----------
def _audit(s: Session, user: str, action: str, doc_id: str | None = None, detail: str = ""):
    s.add(AuditLog(user=user, action=action, doc_id=doc_id, detail=detail))


def _load(doc_id: str, user: str, s: Session) -> tuple[Document, bytes]:
    doc = s.get(Document, doc_id)
    if not doc:
        raise HTTPException(404, "not found")
    path = settings.storage_dir / doc.id
    if not path.exists():
        raise HTTPException(410, "expired or missing")
    return doc, decrypt_from_disk(path)


def _save(doc: Document, data: bytes, s: Session):
    path = settings.storage_dir / doc.id
    doc.size_bytes = encrypt_to_disk(data, path)
    doc.page_count = safe_page_count(data)
    doc.is_encrypted = is_encrypted(data)
    s.add(doc)


# ---------- upload ----------
@router.post("/docs")
async def upload(
    file: UploadFile = File(...),
    persist: bool = Form(False),
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF only")
    data = await file.read()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, "file too large")

    doc = Document(
        id=str(uuid.uuid4()),
        owner=user,
        filename=file.filename,
        persist=persist,
        created_at=datetime.utcnow(),
    )
    _save(doc, data, s)
    _audit(s, user, "upload", doc.id, f"persist={persist} size={len(data)}")
    s.commit()
    return {"id": doc.id, "filename": doc.filename, "page_count": doc.page_count,
            "size_bytes": doc.size_bytes, "is_encrypted": doc.is_encrypted}


# ---------- list / delete ----------
@router.get("/docs")
def list_docs(user: str = Depends(current_user), s: Session = Depends(get_session)):
    rows = s.exec(select(Document).where(Document.owner == user).order_by(Document.created_at.desc())).all()
    return [
        {"id": d.id, "filename": d.filename, "page_count": d.page_count,
         "size_bytes": d.size_bytes, "persist": d.persist,
         "is_encrypted": d.is_encrypted,
         "created_at": d.created_at.isoformat()}
        for d in rows
    ]


@router.delete("/docs/{doc_id}")
def delete_doc(doc_id: str, user: str = Depends(current_user), s: Session = Depends(get_session)):
    doc, _ = _load(doc_id, user, s)
    (settings.storage_dir / doc.id).unlink(missing_ok=True)
    _audit(s, user, "delete", doc.id)
    s.delete(doc)
    s.commit()
    return {"ok": True}


# ---------- analyze ----------
@router.get("/docs/{doc_id}/blanks")
def blanks(doc_id: str, user: str = Depends(current_user), s: Session = Depends(get_session)):
    doc, data = _load(doc_id, user, s)
    info = detect_blank_pages(data)
    _audit(s, user, "blank_detect", doc.id)
    s.commit()
    return {"id": doc.id, "pages": info}


# ---------- edit ----------
@router.post("/docs/{doc_id}/edit")
def edit(
    doc_id: str,
    payload: dict = Body(...),
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    """
    payload:
      keep_order: [int]  optional
      rotations:  {str(index): deg}  optional
      delete:     [int]  optional
      compress:   bool   optional
      gs_quality: str    optional
    """
    doc, data = _load(doc_id, user, s)

    rotations = {int(k): int(v) for k, v in (payload.get("rotations") or {}).items()}
    new_data = apply_page_ops(
        data,
        keep_order=payload.get("keep_order"),
        rotations=rotations,
        delete=payload.get("delete"),
    )

    if payload.get("compress"):
        new_data = compress_with_ghostscript(new_data, payload.get("gs_quality"))

    _save(doc, new_data, s)
    _audit(s, user, "edit", doc.id, str({k: v for k, v in payload.items() if k != "data"}))
    s.commit()
    return {"id": doc.id, "page_count": doc.page_count, "size_bytes": doc.size_bytes}


# ---------- auto blank-removal helper ----------
@router.post("/docs/{doc_id}/remove-blanks")
def remove_blanks(doc_id: str, user: str = Depends(current_user), s: Session = Depends(get_session)):
    doc, data = _load(doc_id, user, s)
    info = detect_blank_pages(data)
    drop = [p["index"] for p in info if p["blank"]]
    if not drop:
        return {"id": doc.id, "removed": []}
    new_data = apply_page_ops(data, delete=drop)
    _save(doc, new_data, s)
    _audit(s, user, "remove_blanks", doc.id, f"removed={drop}")
    s.commit()
    return {"id": doc.id, "removed": drop, "page_count": doc.page_count}


# ---------- compress only ----------
@router.post("/docs/{doc_id}/compress")
def compress(doc_id: str, payload: dict = Body(default={}), user: str = Depends(current_user), s: Session = Depends(get_session)):
    doc, data = _load(doc_id, user, s)
    before = len(data)
    new_data = compress_with_ghostscript(data, payload.get("gs_quality"))
    _save(doc, new_data, s)
    _audit(s, user, "compress", doc.id, f"{before}->{len(new_data)}")
    s.commit()
    return {"id": doc.id, "size_before": before, "size_after": doc.size_bytes}


# ---------- download / preview ----------
@router.get("/docs/{doc_id}/file")
def download(doc_id: str, user: str = Depends(current_user), s: Session = Depends(get_session)):
    doc, data = _load(doc_id, user, s)
    _audit(s, user, "download", doc.id)
    s.commit()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{doc.filename}"'},
    )


# ---------- merge ----------
@router.post("/docs/merge")
def merge(
    payload: dict = Body(...),
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    """payload: {ids: [doc_id, ...], filename?: str, persist?: bool}"""
    ids = payload.get("ids") or []
    if len(ids) < 2:
        raise HTTPException(400, "need >=2 doc ids")
    pdfs: list[bytes] = []
    for did in ids:
        d, data = _load(did, user, s)
        pdfs.append(data)
    merged = merge_pdfs(pdfs)
    new_doc = Document(
        id=str(uuid.uuid4()),
        owner=user,
        filename=payload.get("filename") or f"merged_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf",
        persist=bool(payload.get("persist", False)),
    )
    _save(new_doc, merged, s)
    _audit(s, user, "merge", new_doc.id, f"sources={ids}")
    s.commit()
    return {"id": new_doc.id, "filename": new_doc.filename, "page_count": new_doc.page_count, "size_bytes": new_doc.size_bytes}


# ---------- images -> PDF ----------
@router.post("/docs/from-images")
async def from_images(
    files: List[UploadFile] = File(...),
    persist: bool = Form(False),
    filename: str = Form(""),
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    if not files:
        raise HTTPException(400, "no files")
    payload: list[tuple[str, bytes]] = []
    total = 0
    for f in files:
        ext = "." + f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ALLOWED_IMAGE_EXT:
            raise HTTPException(400, f"unsupported: {f.filename}")
        data = await f.read()
        total += len(data)
        if total > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, "total too large")
        payload.append((f.filename, data))
    try:
        pdf_bytes = images_to_pdf(payload)
    except ValueError as e:
        raise HTTPException(400, str(e))
    doc = Document(
        id=str(uuid.uuid4()),
        owner=user,
        filename=filename or f"images_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pdf",
        persist=persist,
    )
    _save(doc, pdf_bytes, s)
    _audit(s, user, "images_to_pdf", doc.id, f"n={len(payload)}")
    s.commit()
    return {"id": doc.id, "filename": doc.filename, "page_count": doc.page_count, "size_bytes": doc.size_bytes}


# ---------- export pages as images ZIP ----------
@router.get("/docs/{doc_id}/export-images")
def export_images(
    doc_id: str,
    fmt: str = "png",
    dpi: int = 150,
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    doc, data = _load(doc_id, user, s)
    try:
        imgs = pdf_to_images(data, fmt=fmt, dpi=dpi)
    except ValueError as e:
        raise HTTPException(400, str(e))
    buf = io.BytesIO()
    ext = "jpg" if fmt.lower().startswith("j") else "png"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, img in enumerate(imgs, 1):
            zf.writestr(f"page_{i:04d}.{ext}", img)
    buf.seek(0)
    _audit(s, user, "export_images", doc.id, f"fmt={fmt} dpi={dpi}")
    s.commit()
    base = doc.filename.rsplit(".", 1)[0]
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{base}_images.zip"'},
    )


# ---------- Split ----------
@router.post("/docs/{doc_id}/split")
def split(
    doc_id: str,
    payload: dict = Body(...),
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    """
    payload:
      mode: 'single' | 'every' | 'ranges'
      every: int (when mode='every')
      ranges_spec: '1-3,5,7-9' (when mode='ranges'; each comma group → 1 PDF)
      persist: bool
    Returns list of new doc metas.
    """
    doc, data = _load(doc_id, user, s)
    mode = payload.get("mode", "single")
    try:
        parts = split_pdf(
            data,
            mode=mode,
            every=int(payload.get("every", 1)),
            ranges_spec=payload.get("ranges_spec"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    persist = bool(payload.get("persist", False))
    base = doc.filename.rsplit(".", 1)[0]
    out = []
    for i, pdf_bytes in enumerate(parts, 1):
        new = Document(
            id=str(uuid.uuid4()),
            owner=user,
            filename=f"{base}_part{i:03d}.pdf",
            persist=persist,
        )
        _save(new, pdf_bytes, s)
        out.append({"id": new.id, "filename": new.filename,
                    "page_count": new.page_count, "size_bytes": new.size_bytes})
    _audit(s, user, "split", doc.id, f"mode={mode} parts={len(parts)}")
    s.commit()
    return {"parts": out}


# ---------- Extract pages ----------
@router.post("/docs/{doc_id}/extract")
def extract(
    doc_id: str,
    payload: dict = Body(...),
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    """
    payload:
      pages: [int]  (0-based, optional)
      ranges_spec: str  (1-based, e.g. '1-3,5')
      persist: bool
      filename: str  (optional)
    Returns new doc meta.
    """
    doc, data = _load(doc_id, user, s)
    pages = payload.get("pages")
    spec = payload.get("ranges_spec")
    try:
        if spec:
            pages = parse_page_ranges(spec, doc.page_count or 999999)
        if not pages:
            raise HTTPException(400, "no pages selected")
        new_data = extract_pages(data, [int(p) for p in pages])
    except ValueError as e:
        raise HTTPException(400, str(e))

    persist = bool(payload.get("persist", False))
    base = doc.filename.rsplit(".", 1)[0]
    new = Document(
        id=str(uuid.uuid4()),
        owner=user,
        filename=payload.get("filename") or f"{base}_extract.pdf",
        persist=persist,
    )
    _save(new, new_data, s)
    _audit(s, user, "extract", doc.id, f"pages={pages}")
    s.commit()
    return {"id": new.id, "filename": new.filename,
            "page_count": new.page_count, "size_bytes": new.size_bytes}


# ---------- Redact ----------
@router.post("/docs/{doc_id}/redact")
def redact(
    doc_id: str,
    payload: dict = Body(...),
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    """
    payload:
      areas: [{page: int (0-based), x: float, y: float, w: float, h: float}]
        coords NORMALIZED 0..1 (origin top-left)
    Modifies original document in place.
    """
    doc, data = _load(doc_id, user, s)
    try:
        new_data = redact_areas(data, payload.get("areas") or [])
    except ValueError as e:
        raise HTTPException(400, str(e))
    _save(doc, new_data, s)
    _audit(s, user, "redact", doc.id, f"n={len(payload.get('areas') or [])}")
    s.commit()
    return {"id": doc.id, "page_count": doc.page_count, "size_bytes": doc.size_bytes}


# ---------- Protect (set password) ----------
@router.post("/docs/{doc_id}/protect")
def protect(
    doc_id: str,
    payload: dict = Body(...),
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    """payload: {password: str}"""
    doc, data = _load(doc_id, user, s)
    password = (payload.get("password") or "").strip()
    if len(password) < 4:
        raise HTTPException(400, "password too short (min 4 chars)")
    try:
        new_data = protect_pdf(data, password)
    except ValueError as e:
        raise HTTPException(400, str(e))
    _save(doc, new_data, s)
    _audit(s, user, "protect", doc.id, "password set")
    s.commit()
    return {"id": doc.id, "is_encrypted": doc.is_encrypted, "size_bytes": doc.size_bytes}


# ---------- Unlock (remove password) ----------
@router.post("/docs/{doc_id}/unlock")
def unlock(
    doc_id: str,
    payload: dict = Body(...),
    user: str = Depends(current_user),
    s: Session = Depends(get_session),
):
    """payload: {password: str}"""
    doc, data = _load(doc_id, user, s)
    try:
        new_data = unlock_pdf(data, payload.get("password") or "")
    except ValueError as e:
        raise HTTPException(400, str(e))
    _save(doc, new_data, s)
    _audit(s, user, "unlock", doc.id, "password removed")
    s.commit()
    return {"id": doc.id, "is_encrypted": doc.is_encrypted,
            "page_count": doc.page_count, "size_bytes": doc.size_bytes}
