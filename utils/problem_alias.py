import base64
import hashlib
import hmac
import os


def make_problem_alias(problem_name: str, benchmark_family: str | None) -> str:
    salt = os.getenv("PROBLEM_ALIAS_SALT", "").encode("utf-8")
    raw = f"{benchmark_family or 'unknown'}:{problem_name}".encode("utf-8")

    if salt:
        digest = hmac.new(salt, raw, hashlib.sha256).digest()
    else:
        digest = hashlib.sha256(raw).digest()

    return base64.b32encode(digest).decode("ascii").rstrip("=")[:5]
