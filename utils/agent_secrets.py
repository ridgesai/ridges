from __future__ import annotations

import base64
import os
from typing import Final

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AGENT_SECRET_VERSION: Final[int] = 1
NONCE_SIZE_BYTES: Final[int] = 12
MASTER_KEY_SIZE_BYTES: Final[int] = 32
MASTER_KEY_ENV_VAR: Final[str] = "RIDGES_AGENT_KEY_ENCRYPTION_KEY"


class AgentKeyEncryptionConfigError(RuntimeError):
    """Raised when the platform encryption key is missing or invalid."""


class AgentKeyDecryptError(ValueError):
    """Raised when an encrypted agent secret cannot be decrypted."""


def _load_master_key() -> bytes:
    encoded = (os.getenv(MASTER_KEY_ENV_VAR) or "").strip()
    if not encoded:
        raise AgentKeyEncryptionConfigError(f"Missing required environment variable: {MASTER_KEY_ENV_VAR}")

    try:
        key = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise AgentKeyEncryptionConfigError(f"{MASTER_KEY_ENV_VAR} must be a valid base64-encoded 32-byte key") from exc

    if len(key) != MASTER_KEY_SIZE_BYTES:
        raise AgentKeyEncryptionConfigError(
            f"{MASTER_KEY_ENV_VAR} must decode to exactly {MASTER_KEY_SIZE_BYTES} bytes"
        )

    return key


def encrypt_openrouter_api_key(plaintext: str) -> bytes:
    nonce = os.urandom(NONCE_SIZE_BYTES)
    ciphertext = AESGCM(_load_master_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return bytes([AGENT_SECRET_VERSION]) + nonce + ciphertext


def decrypt_openrouter_api_key(ciphertext_blob: bytes) -> str:
    if len(ciphertext_blob) <= 1 + NONCE_SIZE_BYTES:
        raise AgentKeyDecryptError("Ciphertext blob is truncated")

    version = ciphertext_blob[0]
    if version != AGENT_SECRET_VERSION:
        raise AgentKeyDecryptError(f"Unsupported ciphertext version: {version}")

    nonce = ciphertext_blob[1 : 1 + NONCE_SIZE_BYTES]
    ciphertext = ciphertext_blob[1 + NONCE_SIZE_BYTES :]

    try:
        plaintext = AESGCM(_load_master_key()).decrypt(nonce, ciphertext, None)
    except AgentKeyEncryptionConfigError:
        raise
    except Exception as exc:
        raise AgentKeyDecryptError("Failed to decrypt agent secret") from exc

    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentKeyDecryptError("Decrypted agent secret is not valid UTF-8") from exc
