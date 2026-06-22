from cryptography.fernet import Fernet

import config


def encrypt_token(plain: str) -> str:
    try:
        fernet = Fernet(config.SECRET_KEY.encode())
        return fernet.encrypt(plain.encode()).decode()
    except Exception as exc:
        raise ValueError("Failed to encrypt token") from exc


def decrypt_token(encrypted: str) -> str:
    try:
        fernet = Fernet(config.SECRET_KEY.encode())
        return fernet.decrypt(encrypted.encode()).decode()
    except Exception as exc:
        raise ValueError("Failed to decrypt token") from exc
