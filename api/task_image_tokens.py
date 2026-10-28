"""Short-lived bearer tokens for the task image registry."""

import base64
import json
import re
import time

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

import api.config as config
from ridges_harbor.task_image_contract import RECIPE, SERVICE_RE

TOKEN_TTL_SECONDS = 2100

REPOSITORY_RE = re.compile(rf"tasks/{RECIPE}/(?P<hex>[0-9a-f]{{64}})/(?:agent|verifier)/{SERVICE_RE.pattern}")


class TokenSigningUnavailable(RuntimeError):
    pass


def repository_task_digest(repository: str) -> str | None:
    """Return the task digest a task image repository belongs to, or None."""
    match = REPOSITORY_RE.fullmatch(repository)
    return f"sha256:{match['hex']}" if match else None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _signing_key() -> Ed25519PrivateKey:
    if not config.TASK_IMAGE_TOKEN_SIGNING_KEY:
        raise TokenSigningUnavailable("TASK_IMAGE_TOKEN_SIGNING_KEY is not set")

    try:
        key = load_pem_private_key(config.TASK_IMAGE_TOKEN_SIGNING_KEY.encode(), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise TokenSigningUnavailable("TASK_IMAGE_TOKEN_SIGNING_KEY is not a PEM private key") from exc

    if not isinstance(key, Ed25519PrivateKey):
        raise TokenSigningUnavailable("TASK_IMAGE_TOKEN_SIGNING_KEY must be an Ed25519 key")
    return key


def mint_token(subject: str, access: list[dict]) -> str:
    """Sign a distribution-spec registry token.

    `iat` is omitted: Zot rejects an `iat` in its future, so API clock skew
    would otherwise break fresh tokens.
    """
    key = _signing_key()
    header = {"alg": "EdDSA", "typ": "JWT"}
    claims = {"sub": subject, "exp": int(time.time()) + TOKEN_TTL_SECONDS, "access": access}
    signing_input = ".".join(_b64url(json.dumps(part, separators=(",", ":")).encode()) for part in (header, claims))
    return f"{signing_input}.{_b64url(key.sign(signing_input.encode()))}"
