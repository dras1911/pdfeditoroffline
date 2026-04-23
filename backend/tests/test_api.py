import io


def upload(client, pdf_bytes, name="test.pdf", persist=False):
    r = client.post(
        "/api/docs",
        files={"file": (name, pdf_bytes, "application/pdf")},
        data={"persist": str(persist).lower()},
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_health(client):
    assert client.get("/health").json() == {"ok": True}


def test_upload_and_list(authed_client, sample_pdf_bytes):
    meta = upload(authed_client, sample_pdf_bytes)
    assert meta["page_count"] == 3
    docs = authed_client.get("/api/docs").json()
    assert any(d["id"] == meta["id"] for d in docs)


def test_upload_rejects_non_pdf(authed_client):
    r = authed_client.post(
        "/api/docs",
        files={"file": ("x.txt", b"hi", "text/plain")},
        data={"persist": "false"},
    )
    assert r.status_code == 400


def test_blanks_endpoint(authed_client, sample_pdf_bytes):
    meta = upload(authed_client, sample_pdf_bytes)
    j = authed_client.get(f"/api/docs/{meta['id']}/blanks").json()
    assert sum(1 for p in j["pages"] if p["blank"]) == 1


def test_remove_blanks(authed_client, sample_pdf_bytes):
    meta = upload(authed_client, sample_pdf_bytes)
    j = authed_client.post(f"/api/docs/{meta['id']}/remove-blanks").json()
    assert j["removed"] == [1]
    assert j["page_count"] == 2


def test_edit_reorder_rotate_delete(authed_client, sample_pdf_bytes):
    meta = upload(authed_client, sample_pdf_bytes)
    r = authed_client.post(f"/api/docs/{meta['id']}/edit", json={
        "keep_order": [2, 0],
        "rotations": {"2": 90},
    })
    assert r.status_code == 200
    assert r.json()["page_count"] == 2


def test_merge(authed_client, sample_pdf_bytes):
    a = upload(authed_client, sample_pdf_bytes, "a.pdf")
    b = upload(authed_client, sample_pdf_bytes, "b.pdf")
    r = authed_client.post("/api/docs/merge", json={"ids": [a["id"], b["id"]], "filename": "out.pdf"})
    assert r.status_code == 200
    assert r.json()["page_count"] == 6


def test_merge_needs_two(authed_client, sample_pdf_bytes):
    a = upload(authed_client, sample_pdf_bytes)
    r = authed_client.post("/api/docs/merge", json={"ids": [a["id"]]})
    assert r.status_code == 400


def test_images_to_pdf(authed_client, sample_image_bytes):
    r = authed_client.post(
        "/api/docs/from-images",
        files=[
            ("files", ("a.png", sample_image_bytes, "image/png")),
            ("files", ("b.png", sample_image_bytes, "image/png")),
        ],
        data={"persist": "false"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["page_count"] == 2


def test_images_unsupported(authed_client, sample_image_bytes):
    r = authed_client.post(
        "/api/docs/from-images",
        files=[("files", ("a.gif", sample_image_bytes, "image/gif"))],
    )
    assert r.status_code == 400


def test_export_images_zip(authed_client, sample_pdf_bytes):
    meta = upload(authed_client, sample_pdf_bytes)
    r = authed_client.get(f"/api/docs/{meta['id']}/export-images?fmt=png&dpi=72")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert len(z.namelist()) == 3


def test_download(authed_client, sample_pdf_bytes):
    meta = upload(authed_client, sample_pdf_bytes)
    r = authed_client.get(f"/api/docs/{meta['id']}/file")
    assert r.status_code == 200
    assert r.content[:4] == b"%PDF"


def test_delete(authed_client, sample_pdf_bytes):
    meta = upload(authed_client, sample_pdf_bytes)
    r = authed_client.delete(f"/api/docs/{meta['id']}")
    assert r.status_code == 200
    r2 = authed_client.get(f"/api/docs/{meta['id']}/file")
    assert r2.status_code == 404
