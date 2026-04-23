from app.pdf_ops import (
    detect_blank_pages, apply_page_ops, merge_pdfs, images_to_pdf,
    pdf_to_images, page_count,
    parse_page_ranges, extract_pages, split_pdf,
    redact_areas, protect_pdf, unlock_pdf, is_encrypted, safe_page_count,
)


def test_blank_detect(sample_pdf_bytes):
    info = detect_blank_pages(sample_pdf_bytes)
    assert len(info) == 3
    assert info[0]["blank"] is False
    assert info[1]["blank"] is True
    assert info[2]["blank"] is False


def test_apply_delete(sample_pdf_bytes):
    out = apply_page_ops(sample_pdf_bytes, delete=[1])
    assert page_count(out) == 2


def test_apply_reorder_and_rotate(sample_pdf_bytes):
    out = apply_page_ops(sample_pdf_bytes, keep_order=[2, 0], rotations={2: 90})
    assert page_count(out) == 2


def test_apply_invalid_index(sample_pdf_bytes):
    import pytest
    with pytest.raises(ValueError):
        apply_page_ops(sample_pdf_bytes, keep_order=[99])


def test_merge(sample_pdf_bytes):
    out = merge_pdfs([sample_pdf_bytes, sample_pdf_bytes])
    assert page_count(out) == 6


def test_merge_empty():
    import pytest
    with pytest.raises(ValueError):
        merge_pdfs([])


def test_images_to_pdf(sample_image_bytes):
    out = images_to_pdf([("a.png", sample_image_bytes), ("b.png", sample_image_bytes)])
    assert out[:4] == b"%PDF"
    assert page_count(out) == 2


def test_images_to_pdf_unsupported(sample_image_bytes):
    import pytest
    with pytest.raises(ValueError):
        images_to_pdf([("a.gif", sample_image_bytes)])


def test_pdf_to_images(sample_pdf_bytes):
    imgs = pdf_to_images(sample_pdf_bytes, fmt="png", dpi=72)
    assert len(imgs) == 3
    assert imgs[0][:8] == b"\x89PNG\r\n\x1a\n"


# ---------- Split / Extract ----------

def test_parse_page_ranges():
    assert parse_page_ranges("1-3,5", 10) == [0, 1, 2, 4]
    assert parse_page_ranges("3", 10) == [2]
    assert parse_page_ranges("1-2,2-3", 10) == [0, 1, 2]  # dedup


def test_parse_page_ranges_invalid():
    import pytest
    with pytest.raises(ValueError): parse_page_ranges("", 10)
    with pytest.raises(ValueError): parse_page_ranges("99", 10)
    with pytest.raises(ValueError): parse_page_ranges("3-1", 10)
    with pytest.raises(ValueError): parse_page_ranges("a", 10)


def test_extract_pages(sample_pdf_bytes):
    out = extract_pages(sample_pdf_bytes, [0, 2])
    assert page_count(out) == 2


def test_split_single(sample_pdf_bytes):
    parts = split_pdf(sample_pdf_bytes, mode="single")
    assert len(parts) == 3
    assert all(page_count(p) == 1 for p in parts)


def test_split_every(sample_pdf_bytes):
    parts = split_pdf(sample_pdf_bytes, mode="every", every=2)
    assert len(parts) == 2
    assert page_count(parts[0]) == 2 and page_count(parts[1]) == 1


def test_split_ranges(sample_pdf_bytes):
    parts = split_pdf(sample_pdf_bytes, mode="ranges", ranges_spec="1-2,3")
    assert len(parts) == 2
    assert page_count(parts[0]) == 2 and page_count(parts[1]) == 1


# ---------- Redact ----------

def test_redact_areas(sample_pdf_bytes):
    areas = [{"page": 0, "x": 0.1, "y": 0.1, "w": 0.3, "h": 0.1}]
    out = redact_areas(sample_pdf_bytes, areas)
    assert out[:4] == b"%PDF"
    assert page_count(out) == 3


def test_redact_invalid_page(sample_pdf_bytes):
    import pytest
    with pytest.raises(ValueError):
        redact_areas(sample_pdf_bytes, [{"page": 99, "x": 0, "y": 0, "w": 0.1, "h": 0.1}])


def test_redact_empty(sample_pdf_bytes):
    import pytest
    with pytest.raises(ValueError):
        redact_areas(sample_pdf_bytes, [])


# ---------- Protect / Unlock ----------

def test_protect_then_unlock(sample_pdf_bytes):
    enc = protect_pdf(sample_pdf_bytes, "secret123")
    assert is_encrypted(enc)
    assert safe_page_count(enc) == 0  # encrypted → unknown
    dec = unlock_pdf(enc, "secret123")
    assert not is_encrypted(dec)
    assert page_count(dec) == 3


def test_unlock_wrong_password(sample_pdf_bytes):
    import pytest
    enc = protect_pdf(sample_pdf_bytes, "secret123")
    with pytest.raises(ValueError):
        unlock_pdf(enc, "wrong")


def test_protect_already_encrypted(sample_pdf_bytes):
    import pytest
    enc = protect_pdf(sample_pdf_bytes, "x123")
    with pytest.raises(ValueError):
        protect_pdf(enc, "y456")


def test_unlock_unencrypted_noop(sample_pdf_bytes):
    out = unlock_pdf(sample_pdf_bytes, "")
    assert page_count(out) == 3
