from pathlib import Path

from cryptography.fernet import Fernet

from app.config import get_settings


class SecretCipher:
    def __init__(self) -> None:
        key = get_settings().encryption_key
        if not key:
            key = self._load_or_create_local_key()
        self._fernet = Fernet(key.encode())

    def _load_or_create_local_key(self) -> str:
        key_path = Path(".secrets/fernet.key")
        key_path.parent.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            return key_path.read_text(encoding="utf-8").strip()
        generated = Fernet.generate_key().decode()
        key_path.write_text(generated, encoding="utf-8")
        return generated

    def encrypt(self, raw: str) -> str:
        return self._fernet.encrypt(raw.encode()).decode()

    def decrypt(self, encrypted: str) -> str:
        return self._fernet.decrypt(encrypted.encode()).decode()
