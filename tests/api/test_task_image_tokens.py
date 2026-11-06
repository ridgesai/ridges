import base64
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient

import api.config as config
from api.endpoints import task_images
from api.endpoints import validator as validator_endpoint
from api.endpoints.validator_models import ValidatorTaskDownloadUrlRequest, ValidatorTaskImageTokenRequest
from api.task_image_tokens import TOKEN_TTL_SECONDS

DIGEST = "sha256:" + "a" * 64
REPOSITORY = f"tasks/v1/{'a' * 64}/agent/main"
OTHER_REPOSITORY = f"tasks/v1/{'b' * 64}/verifier/main"


@pytest.fixture
def signing_key(monkeypatch):
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    monkeypatch.setattr(config, "TASK_IMAGE_TOKEN_SIGNING_KEY", pem)
    return key.public_key()


def _claims(token: str, public_key) -> dict:
    def decode(part: str) -> bytes:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    header, claims, signature = token.split(".")
    public_key.verify(decode(signature), f"{header}.{claims}".encode())
    assert json.loads(decode(header)) == {"alg": "EdDSA", "typ": "JWT"}
    return json.loads(decode(claims))


def _validator(monkeypatch, *, specs=({"task_digest": DIGEST, "s3_key": "tasks/a.tar.gz"},)):
    evaluation_id = uuid4()

    async def runs(requested_evaluation_id):
        assert requested_evaluation_id == evaluation_id
        return [SimpleNamespace(execution_spec=spec) for spec in specs]

    monkeypatch.setattr(validator_endpoint, "get_all_evaluation_runs_in_evaluation_id", runs)
    return validator_endpoint.Validator(
        session_id=uuid4(),
        name="validator",
        hotkey="validator-hotkey",
        time_connected=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        current_evaluation_id=evaluation_id,
    )


@pytest.mark.anyio
async def test_validator_token_grants_pull_on_one_repository_of_its_current_task(monkeypatch, signing_key):
    validator = _validator(monkeypatch)

    response = await validator_endpoint.validator_task_image_token(
        ValidatorTaskImageTokenRequest(repository=REPOSITORY), Response(), validator=validator
    )

    claims = _claims(response.token, signing_key)
    assert claims["sub"] == "validator-hotkey"
    assert claims["access"] == [{"type": "repository", "name": REPOSITORY, "actions": ["pull"]}]
    assert 0 < claims["exp"] - time.time() <= TOKEN_TTL_SECONDS
    assert "iat" not in claims


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("repository", "evaluation", "status"),
    [
        (OTHER_REPOSITORY, "current", 404),
        (f"tasks/v1/{'a' * 64}/agent/main/extra", "current", 422),
        (f"tasks/v2/{'a' * 64}/agent/main", "current", 422),
        (f"tasks/v1/{'a' * 64}/agent/Main", "current", 422),
        (f"other/v1/{'a' * 64}/agent/main", "current", 422),
        (REPOSITORY, None, 409),
    ],
)
async def test_validator_token_is_refused_outside_its_current_evaluation(
    monkeypatch, signing_key, repository, evaluation, status
):
    validator = _validator(monkeypatch)
    if evaluation is None:
        validator.current_evaluation_id = None

    with pytest.raises(HTTPException) as error:
        await validator_endpoint.validator_task_image_token(
            ValidatorTaskImageTokenRequest(repository=repository), Response(), validator=validator
        )

    assert error.value.status_code == status


def _non_ed25519_key() -> str:
    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        .decode()
    )


@pytest.mark.anyio
@pytest.mark.parametrize("key", ["", "not a pem key", _non_ed25519_key()])
async def test_validator_token_fails_closed_without_a_usable_signing_key(monkeypatch, key):
    monkeypatch.setattr(config, "TASK_IMAGE_TOKEN_SIGNING_KEY", key)

    with pytest.raises(HTTPException) as error:
        await validator_endpoint.validator_task_image_token(
            ValidatorTaskImageTokenRequest(repository=REPOSITORY), Response(), validator=_validator(monkeypatch)
        )

    assert error.value.status_code == 503


def test_validator_token_over_http_requires_a_session_and_is_not_cached(monkeypatch, signing_key):
    validator = _validator(monkeypatch)
    monkeypatch.setattr(validator_endpoint, "SESSION_ID_TO_VALIDATOR", {validator.session_id: validator})
    app = FastAPI()
    app.include_router(validator_endpoint.router, prefix="/validator")

    with TestClient(app) as client:
        unknown = client.post(
            "/validator/task-image-token",
            json={"repository": REPOSITORY},
            headers={"Authorization": f"Bearer {uuid4()}"},
        )
        granted = client.post(
            "/validator/task-image-token",
            json={"repository": REPOSITORY},
            headers={"Authorization": f"Bearer {validator.session_id}"},
        )

    assert unknown.status_code == 401
    assert granted.status_code == 200
    assert granted.headers["cache-control"] == "no-store"
    assert _claims(granted.json()["token"], signing_key)["access"][0]["name"] == REPOSITORY


@pytest.mark.anyio
async def test_task_download_url_uses_the_same_current_evaluation_lookup(monkeypatch):
    async def presign(s3_key, ttl_seconds):
        return f"https://storage/{s3_key}?ttl={ttl_seconds}"

    monkeypatch.setattr(validator_endpoint, "generate_presigned_url", presign)

    response = await validator_endpoint.validator_task_download_url(
        ValidatorTaskDownloadUrlRequest(task_digest=DIGEST), validator=_validator(monkeypatch)
    )
    assert response.url == "https://storage/tasks/a.tar.gz?ttl=300"

    with pytest.raises(HTTPException) as error:
        await validator_endpoint.validator_task_download_url(
            ValidatorTaskDownloadUrlRequest(task_digest=DIGEST),
            validator=_validator(monkeypatch, specs=({"task_digest": DIGEST},)),
        )
    assert error.value.status_code == 422


@pytest.fixture
def publisher_client(monkeypatch, signing_key):
    monkeypatch.setattr(config, "TASK_IMAGE_PUBLISHER_PASSWORD", "publisher-password")
    app = FastAPI()
    app.include_router(task_images.router, prefix="/task-images")
    with TestClient(app) as client:
        yield client


def test_publisher_login_without_scope_gets_a_token_with_no_access(publisher_client, signing_key):
    response = publisher_client.get(
        "/task-images/token",
        params={"service": "registry", "offline_token": "true", "client_id": "docker", "account": "publisher"},
        auth=("publisher", "publisher-password"),
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"token", "access_token", "expires_in"}
    assert body["token"] == body["access_token"]
    assert body["expires_in"] == TOKEN_TTL_SECONDS
    claims = _claims(body["token"], signing_key)
    assert claims["sub"] == "publisher"
    assert claims["access"] == []


def test_publisher_token_grants_only_pull_and_push_on_task_image_repositories(publisher_client, signing_key):
    response = publisher_client.get(
        "/task-images/token",
        params=[
            ("scope", f"repository:{REPOSITORY}:pull,push,delete"),
            ("scope", f"repository:{OTHER_REPOSITORY}:pull repository:other/image:pull,push"),
            ("scope", f"repository:{REPOSITORY.replace('agent', 'verifier')}:delete"),
            ("scope", "registry:catalog:*"),
            ("scope", ""),
        ],
        auth=("publisher", "publisher-password"),
    )

    assert response.status_code == 200
    assert _claims(response.json()["token"], signing_key)["access"] == [
        {"type": "repository", "name": REPOSITORY, "actions": ["pull", "push"]},
        {"type": "repository", "name": OTHER_REPOSITORY, "actions": ["pull"]},
    ]


@pytest.mark.parametrize("auth", [None, ("publisher", "wrong"), ("publisher", "")])
def test_publisher_token_requires_the_publisher_password(publisher_client, auth):
    response = publisher_client.get("/task-images/token", auth=auth)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == 'Basic realm="task-images"'
    assert "token" not in response.json()


def test_publisher_token_fails_closed_when_unconfigured(publisher_client, monkeypatch):
    monkeypatch.setattr(config, "TASK_IMAGE_PUBLISHER_PASSWORD", "")

    response = publisher_client.get("/task-images/token", auth=("publisher", ""))

    assert response.status_code == 503


def test_publisher_token_rejects_oauth_post_so_clients_fall_back_to_get(publisher_client):
    # BuildKit and containerd try OAuth POST first and retry with GET on 405.
    response = publisher_client.post("/task-images/token", auth=("publisher", "publisher-password"))

    assert response.status_code == 405
