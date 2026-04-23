from cryptography.fernet import Fernet
from pathlib import Path
from .config import settings

_fernet = Fernet(settings.encryption_key.encode())


def encrypt_to_disk(data: bytes, path: Path) -> int:
    token = _fernet.encrypt(data)
    path.write_bytes(token)
    return len(token)


def decrypt_from_disk(path: Path) -> bytes:
    return _fernet.decrypt(path.read_bytes())
