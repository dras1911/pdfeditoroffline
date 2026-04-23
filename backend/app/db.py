from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, create_engine, Session
from sqlalchemy import text
from .config import settings


class Document(SQLModel, table=True):
    id: str = Field(primary_key=True)
    owner: str = Field(index=True)
    filename: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    persist: bool = False  # False = session, True = saved (TTL still applies)
    size_bytes: int = 0
    page_count: int = 0
    is_encrypted: bool = False


class AuditLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    user: str = Field(index=True)
    action: str
    doc_id: Optional[str] = None
    detail: str = ""


engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})


def init_db():
    SQLModel.metadata.create_all(engine)
    # cheap migration: add columns if missing (SQLite specific)
    with engine.connect() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(document)").fetchall()]
        if "is_encrypted" not in cols:
            conn.exec_driver_sql("ALTER TABLE document ADD COLUMN is_encrypted BOOLEAN DEFAULT 0")
            conn.commit()


def get_session():
    with Session(engine) as s:
        yield s
