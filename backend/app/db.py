from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, create_engine, Session
from .config import settings


class Document(SQLModel, table=True):
    id: str = Field(primary_key=True)
    owner: str = Field(index=True)
    filename: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    persist: bool = False  # False = session, True = saved (TTL still applies)
    size_bytes: int = 0
    page_count: int = 0


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


def get_session():
    with Session(engine) as s:
        yield s
