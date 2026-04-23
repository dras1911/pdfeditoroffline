import io
import os
import tempfile
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from PIL import Image

TMP = tempfile.mkdtemp(prefix="pdftools_test_")
os.environ["PDFTOOLS_STORAGE_DIR"] = str(Path(TMP) / "sessions")
os.environ["PDFTOOLS_DB_PATH"] = str(Path(TMP) / "meta.db")
os.environ["PDFTOOLS_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["PDFTOOLS_TTL_HOURS"] = "24"
os.environ["PDFTOOLS_PURGE_INTERVAL_MINUTES"] = "9999"


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    import fitz
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 100), "Hello page 1 content")
    doc.new_page()
    p3 = doc.new_page()
    p3.insert_text((72, 100), "Page 3 text body")
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def sample_image_bytes() -> bytes:
    img = Image.new("RGB", (200, 200), (200, 50, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import init_db
    init_db()
    return TestClient(app)


@pytest.fixture
def authed_client(client):
    return client
