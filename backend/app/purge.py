"""TTL purge: delete docs older than settings.ttl_hours."""
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from sqlmodel import Session, select

from .config import settings
from .db import engine, Document, AuditLog


def purge_expired():
    cutoff = datetime.utcnow() - timedelta(hours=settings.ttl_hours)
    with Session(engine) as s:
        old = s.exec(select(Document).where(Document.created_at < cutoff)).all()
        for d in old:
            path = settings.storage_dir / d.id
            if path.exists():
                # best-effort secure delete: overwrite then unlink
                try:
                    size = path.stat().st_size
                    with open(path, "r+b") as f:
                        f.write(b"\0" * size)
                except OSError:
                    pass
                path.unlink(missing_ok=True)
            s.add(AuditLog(user="system", action="purge", doc_id=d.id))
            s.delete(d)
        s.commit()


def start_scheduler():
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(purge_expired, "interval", minutes=settings.purge_interval_minutes, id="purge")
    sched.start()
    return sched
