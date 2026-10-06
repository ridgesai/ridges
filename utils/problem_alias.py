import base64
import hashlib
import hmac
import logging
import os
import secrets
from functools import cache

from utils.test_alias_words import TEST_ALIAS_ADJECTIVES, TEST_ALIAS_NOUNS

logger = logging.getLogger(__name__)
_RUNTIME_ALIAS_SALT = secrets.token_bytes(32)


@cache
def _runtime_alias_salt() -> bytes:
    logger.warning(
        "PROBLEM_ALIAS_SALT is unset or empty; using a random process-local salt. "
        "Set a shared secret salt for stable aliases across workers and restarts."
    )
    return _RUNTIME_ALIAS_SALT


def _make_digest(raw: str) -> bytes:
    salt = os.getenv("PROBLEM_ALIAS_SALT", "").encode("utf-8") or _runtime_alias_salt()
    raw_bytes = raw.encode("utf-8")
    return hmac.new(salt, raw_bytes, hashlib.sha256).digest()


def _base32_digest(digest: bytes) -> str:
    return base64.b32encode(digest).decode("ascii").rstrip("=")


def _make_alias(raw: str, *, length: int) -> str:
    return _base32_digest(_make_digest(raw))[:length]


def _index_from_digest(digest: bytes, *, offset: int, modulo: int) -> int:
    return int.from_bytes(digest[offset : offset + 4], "big") % modulo


def _make_test_alias_raw(
    *,
    benchmark_family: str | None,
    problem_name: str,
    test_name: str,
    test_category: str | None,
) -> str:
    return f"{benchmark_family or 'unknown'}:{problem_name}:{test_category or 'default'}:{test_name}"


def make_problem_alias(problem_name: str, benchmark_family: str | None) -> str:
    return _make_alias(f"{benchmark_family or 'unknown'}:{problem_name}", length=5)


def make_test_alias(
    *,
    benchmark_family: str | None,
    problem_name: str,
    test_name: str,
    test_category: str | None,
) -> str:
    digest = _make_digest(
        _make_test_alias_raw(
            benchmark_family=benchmark_family,
            problem_name=problem_name,
            test_name=test_name,
            test_category=test_category,
        )
    )
    adjective = TEST_ALIAS_ADJECTIVES[_index_from_digest(digest, offset=0, modulo=len(TEST_ALIAS_ADJECTIVES))]
    noun = TEST_ALIAS_NOUNS[_index_from_digest(digest, offset=4, modulo=len(TEST_ALIAS_NOUNS))]
    suffix = _base32_digest(digest)[:3]

    return f"{adjective}-{noun}-{suffix}".upper()
