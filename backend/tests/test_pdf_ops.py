from app.pdf_ops import (
    detect_blank_pages, apply_page_ops, merge_pdfs, images_to_pdf,
    pdf_to_images, page_count,
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
