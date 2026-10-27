"""Optional image-cache contract, mirrored in platform/ridges_harbor."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

RECIPE = "v1"
PLATFORMS = ("linux/amd64", "linux/arm64")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
SERVICE_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def validate_registry(registry: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]{1,5})?", registry):
        raise ValueError("Registry must be a lowercase hostname[:port], without credentials or a URL scheme")
    return registry


def image_ref(registry: str, task_digest: str, role: str, service: str, platform: str) -> str:
    validate_registry(registry)
    if not DIGEST_RE.fullmatch(task_digest):
        raise ValueError("Expected a full sha256 task digest")

    if role not in ("agent", "verifier") or not SERVICE_RE.fullmatch(service):
        raise ValueError("Invalid task image role/service")

    if platform not in PLATFORMS:
        raise ValueError(f"Unsupported task image platform: {platform}")

    return f"{registry}/tasks/{RECIPE}/{task_digest[7:]}/{role}/{service}:{platform.replace('/', '-')}"


def image_labels(task_digest: str, role: str, service: str) -> dict[str, str]:
    return {
        "org.ridges.task.digest": task_digest,
        "org.ridges.task.role": role,
        "org.ridges.task.service": service,
        "org.ridges.task.recipe": RECIPE,
    }


def environment_builds(environment_dir: Path, docker_image: str | None) -> dict[str, str]:
    """Return service -> Dockerfile for the portable, materialized build subset.

    Existing image: sidecars and task-level prebuilt images keep their original
    references. The proxy and runtime networking remain execution concerns.
    """
    builds = {}
    if not docker_image:
        if not (environment_dir / "Dockerfile").is_file():
            raise ValueError("Image cache requires a main Dockerfile or task-level docker_image")
        builds["main"] = "Dockerfile"
    compose_path = environment_dir / "docker-compose.yaml"

    if not compose_path.exists():
        return builds
    compose = yaml.safe_load(compose_path.read_text())

    if not isinstance(compose, dict) or not isinstance(compose.get("services"), dict):
        raise ValueError("Expected a Compose services mapping")

    for name, service in compose["services"].items():
        if not isinstance(name, str) or not isinstance(service, dict):
            raise ValueError("Invalid Compose service")

        if name == "main" and ("build" in service or "image" in service):
            raise ValueError("Task main service overrides the standard image recipe")

        if "build" not in service:
            continue

        if not SERVICE_RE.fullmatch(name) or name in (
            "sandbox-proxy",
            "proxy",
            "iptables-init",
        ):
            raise ValueError("Unsupported built service")

        build = service["build"]
        if (
            "image" in service
            or not isinstance(build, dict)
            or set(build) - {"context", "dockerfile"}
            or build.get("context", ".") != "."
        ):
            raise ValueError(f"Unsupported build definition for {name}")

        dockerfile = build.get("dockerfile")
        if (
            not isinstance(dockerfile, str)
            or not re.fullmatch(r"[A-Za-z0-9._-]+", dockerfile)
            or dockerfile in (".", "..")
            or not (environment_dir / dockerfile).is_file()
        ):
            raise ValueError(f"Missing or invalid Dockerfile for {name}")
        builds[name] = dockerfile
    return builds
