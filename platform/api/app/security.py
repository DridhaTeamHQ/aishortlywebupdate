from cryptography.fernet import Fernet


def build_fernet_from_app_key(key: str) -> Fernet:
    if not key:
        raise ValueError("PLATFORM_ENCRYPTION_KEY is required")
    return Fernet(key)


def encrypt_json(fernet: Fernet, plaintext: str) -> str:
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_json(fernet: Fernet, ciphertext: str) -> str:
    return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
