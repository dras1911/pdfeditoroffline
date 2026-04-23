from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="PDFTOOLS_")

    storage_dir: Path = Path("/var/lib/pdftools/sessions")
    db_path: Path = Path("/var/lib/pdftools/meta.db")
    encryption_key: str = ""  # Fernet key. If empty + dev_mode, auto-generated.
    ttl_hours: int = 24
    purge_interval_minutes: int = 30

    ghostscript_bin: str = "gs"
    gs_quality: str = "/ebook"  # /screen /ebook /printer /prepress

    blank_text_threshold: int = 5      # chars
    blank_pixel_std_threshold: float = 3.0
    render_dpi: int = 100

    max_upload_mb: int = 200
    cors_origins: str = "http://localhost:5173"

    # Dev mode: auto-generate ephemeral encryption key if not provided.
    dev_mode: bool = False


settings = Settings()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
settings.db_path.parent.mkdir(parents=True, exist_ok=True)

if not settings.encryption_key:
    if settings.dev_mode:
        from cryptography.fernet import Fernet
        settings.encryption_key = Fernet.generate_key().decode()
        import logging
        logging.warning("DEV_MODE: generated ephemeral encryption key (data lost on restart)")
    else:
        raise RuntimeError("PDFTOOLS_ENCRYPTION_KEY is required in production")
