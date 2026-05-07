from __future__ import annotations

import base64

import pytest

from utils.agent_secrets import (
    AgentKeyDecryptError,
    AgentKeyEncryptionConfigError,
    decrypt_openrouter_api_key,
    encrypt_openrouter_api_key,
)


def _encoded_key(byte: bytes) -> str:
    return base64.b64encode(byte * 32).decode("ascii")


def test_encrypt_decrypt_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY", _encoded_key(b"a"))

    ciphertext = encrypt_openrouter_api_key("sk-or-v1-secret")

    assert ciphertext != b"sk-or-v1-secret"
    assert decrypt_openrouter_api_key(ciphertext) == "sk-or-v1-secret"


def test_decrypt_rejects_corrupted_ciphertext(monkeypatch) -> None:
    monkeypatch.setenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY", _encoded_key(b"b"))

    ciphertext = bytearray(encrypt_openrouter_api_key("sk-or-v1-secret"))
    ciphertext[-1] ^= 0x01

    with pytest.raises(AgentKeyDecryptError):
        decrypt_openrouter_api_key(bytes(ciphertext))


def test_decrypt_rejects_wrong_master_key(monkeypatch) -> None:
    monkeypatch.setenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY", _encoded_key(b"c"))
    ciphertext = encrypt_openrouter_api_key("sk-or-v1-secret")

    monkeypatch.setenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY", _encoded_key(b"d"))
    with pytest.raises(AgentKeyDecryptError):
        decrypt_openrouter_api_key(ciphertext)


def test_encrypt_requires_valid_master_key(monkeypatch) -> None:
    monkeypatch.delenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY", raising=False)

    with pytest.raises(AgentKeyEncryptionConfigError):
        encrypt_openrouter_api_key("sk-or-v1-secret")


def test_encrypt_rejects_invalid_base64_master_key(monkeypatch) -> None:
    monkeypatch.setenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY", "!!!not-base64!!!")

    with pytest.raises(AgentKeyEncryptionConfigError):
        encrypt_openrouter_api_key("sk-or-v1-secret")


def test_encrypt_rejects_wrong_length_master_key(monkeypatch) -> None:
    monkeypatch.setenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY", base64.b64encode(b"short").decode("ascii"))

    with pytest.raises(AgentKeyEncryptionConfigError):
        encrypt_openrouter_api_key("sk-or-v1-secret")


def test_decrypt_rejects_unknown_ciphertext_version(monkeypatch) -> None:
    monkeypatch.setenv("RIDGES_AGENT_KEY_ENCRYPTION_KEY", _encoded_key(b"e"))
    ciphertext = bytearray(encrypt_openrouter_api_key("sk-or-v1-secret"))
    ciphertext[0] = 2

    with pytest.raises(AgentKeyDecryptError):
        decrypt_openrouter_api_key(bytes(ciphertext))
