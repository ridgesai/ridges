"""Frozen Compose subset for Ridges Kubernetes same-pod sidecars."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

COMPOSE_LEFTOVER_SERVICES = frozenset({"main", "sandbox-proxy"})
RESERVED_SIDECAR_NAMES = frozenset({"main", "proxy", "iptables-init"})
_ALLOWED_TOP_LEVEL_KEYS = frozenset({"services", "networks", "volumes"})
_ALLOWED_SIDECAR_KEYS = frozenset(
    {
        "image",
        "build",
        "environment",
        "healthcheck",
        "volumes",
        "deploy",
        "mem_limit",
        "mem_reservation",
        "networks",
    }
)
_LEFTOVER_INERT_KEYS = frozenset(
    {
        "command",
        "entrypoint",
        "restart",
        "working_dir",
        "hostname",
        "user",
        "labels",
        "depends_on",
        "expose",
    }
)
_ALLOWED_LEFTOVER_KEYS = _ALLOWED_SIDECAR_KEYS | _LEFTOVER_INERT_KEYS
_ALLOWED_BUILD_KEYS = frozenset({"context", "dockerfile"})
_ALLOWED_HEALTHCHECK_KEYS = frozenset({"test", "interval", "timeout", "retries"})
_ALLOWED_DEPLOY_KEYS = frozenset({"resources"})
_ALLOWED_RESOURCES_KEYS = frozenset({"limits", "reservations"})
_ALLOWED_LIMITS_KEYS = frozenset({"memory"})
_ALLOWED_RESERVATIONS_KEYS = frozenset({"memory", "cpus"})
_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_DOCKERFILE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(ns|us|ms|s|m|h)")
_BYTES_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([kmgtpe]i?b?)?$", re.IGNORECASE)
_DURATION_UNIT_SECONDS = {
    "ns": 1e-9,
    "us": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
}
_BINARY_BYTE_UNITS = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "ki": 1024,
    "kib": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "mi": 1024**2,
    "mib": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "gi": 1024**3,
    "gib": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
    "ti": 1024**4,
    "tib": 1024**4,
}


@dataclass(frozen=True)
class ComposeHealthcheck:
    test_script: str
    interval: str | int | float | None = None
    timeout: str | int | float | None = None
    retries: int | None = None


@dataclass(frozen=True)
class TmpfsMount:
    target: str
    size_bytes: int | None


@dataclass(frozen=True)
class ComposeSidecar:
    name: str
    image: str | None
    dockerfile: str | None
    env: dict[str, str]
    healthcheck: ComposeHealthcheck | None
    tmpfs_mounts: tuple[TmpfsMount, ...]
    memory_request_bytes: int | None = None
    memory_limit_bytes: int | None = None
    cpu_request: str | None = None


def parse_duration_seconds(value: str | int | float | None, default: int) -> int:
    """Convert a Compose duration to whole seconds, at least 1."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        if value >= 1e6:
            return max(1, round(value / 1e9))
        return max(1, round(value))

    total = 0.0
    for amount, unit in _DURATION_RE.findall(str(value)):
        total += float(amount) * _DURATION_UNIT_SECONDS[unit]
    if total <= 0:
        return default
    return max(1, round(total))


def parse_compose_bytes(value: Any, *, source: str, field: str) -> int:
    """Parse a Compose memory/tmpfs size into bytes (Docker binary units)."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise RuntimeError(f"{source}: {field} must be a positive integer or size string")
    if isinstance(value, int):
        if value < 1:
            raise RuntimeError(f"{source}: {field} must be a positive integer or size string")
        return value
    if isinstance(value, float):
        if value < 1 or not value.is_integer():
            raise RuntimeError(f"{source}: {field} must be a positive integer or size string")
        return int(value)

    match = _BYTES_RE.fullmatch(value.strip())
    if match is None:
        raise RuntimeError(f"{source}: {field} must be a positive integer or size string")
    amount = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    multiplier = _BINARY_BYTE_UNITS.get(unit)
    if multiplier is None:
        raise RuntimeError(f"{source}: {field} must be a positive integer or size string")
    size = int(amount * multiplier)
    if size < 1:
        raise RuntimeError(f"{source}: {field} must be a positive integer or size string")
    return size


def parse_compose_cpus(value: Any, *, source: str, field: str) -> str:
    """Parse Compose CPU reservations into a Kubernetes millicore quantity."""
    if isinstance(value, bool):
        raise RuntimeError(f"{source}: {field} must be a CPU count or millicore string")
    if isinstance(value, (int, float)):
        cores = float(value)
        if not math.isfinite(cores):
            raise RuntimeError(f"{source}: {field} must be a CPU count or millicore string")
        millicores = int(round(cores * 1000))
        if millicores < 1:
            raise RuntimeError(f"{source}: {field} must be a CPU count or millicore string")
        return f"{millicores}m"
    if not isinstance(value, str):
        raise RuntimeError(f"{source}: {field} must be a CPU count or millicore string")
    stripped = value.strip()
    if stripped.endswith("m") and stripped[:-1].isdigit() and int(stripped[:-1]) >= 1:
        return stripped
    try:
        cores = float(stripped)
    except ValueError as exc:
        raise RuntimeError(f"{source}: {field} must be a CPU count or millicore string") from exc
    if not math.isfinite(cores):
        raise RuntimeError(f"{source}: {field} must be a CPU count or millicore string")
    millicores = int(round(cores * 1000))
    if millicores < 1:
        raise RuntimeError(f"{source}: {field} must be a CPU count or millicore string")
    return f"{millicores}m"


def k8s_memory_quantity(size_bytes: int) -> str:
    """Format bytes as a Kubernetes memory quantity."""
    if size_bytes % 1024**3 == 0:
        return f"{size_bytes // 1024**3}Gi"
    if size_bytes % 1024**2 == 0:
        return f"{size_bytes // 1024**2}Mi"
    if size_bytes % 1024 == 0:
        return f"{size_bytes // 1024}Ki"
    return str(size_bytes)


def sidecar_image_role(environment_role: str, service_name: str) -> str:
    """Role tag fragment for a sidecar image, e.g. ``agent-postgres``."""
    return f"{environment_role}-{service_name}"


def parse_kubernetes_compose(path: Path) -> list[ComposeSidecar]:
    """Parse a task Compose file into Kubernetes sidecar specs."""
    if not path.exists():
        return []
    return parse_kubernetes_compose_text(path.read_text(), source=str(path))


def parse_kubernetes_compose_text(text: str, *, source: str = "docker-compose.yaml") -> list[ComposeSidecar]:
    """Parse Compose YAML text into Kubernetes sidecar specs."""
    raw = yaml.safe_load(text)
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise RuntimeError(f"{source}: Compose file must be a mapping")

    unknown_top = sorted(set(raw) - _ALLOWED_TOP_LEVEL_KEYS)
    if unknown_top:
        raise RuntimeError(f"{source}: unsupported top-level keys {unknown_top}")

    for inert_key in ("networks", "volumes"):
        if inert_key in raw and not isinstance(raw[inert_key], Mapping):
            raise RuntimeError(f"{source}: {inert_key} must be a mapping")

    services = raw.get("services")
    if services is None:
        return []
    if not isinstance(services, dict):
        raise RuntimeError(f"{source}: services must be a mapping")

    sidecars: list[ComposeSidecar] = []
    for name, spec in services.items():
        service_name = str(name)
        if service_name in COMPOSE_LEFTOVER_SERVICES:
            _parse_leftover(service_name, spec, source=source)
            continue
        sidecars.append(_parse_sidecar(service_name, spec, source=source))
    return sidecars


def compose_service_names(path: Path) -> set[str]:
    """Return every service name in a Compose file, including leftovers."""
    if not path.exists():
        return set()
    raw = yaml.safe_load(path.read_text()) or {}
    if not isinstance(raw, dict):
        raise RuntimeError(f"{path}: Compose file must be a mapping")
    services = raw.get("services", {})
    if not isinstance(services, dict):
        raise RuntimeError(f"{path}: services must be a mapping")
    return {str(name) for name in services}


def _require_mapping(name: str, spec: Any, *, source: str) -> dict[str, Any]:
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise RuntimeError(f"{source}: service {name!r} must be a mapping")
    return spec


def _parse_leftover(name: str, spec: Any, *, source: str) -> None:
    spec = _require_mapping(name, spec, source=source)
    unknown = sorted(set(spec) - _ALLOWED_LEFTOVER_KEYS)
    if unknown:
        raise RuntimeError(f"{source}: service {name}: unsupported keys {unknown}")
    if "volumes" in spec:
        if not isinstance(spec.get("volumes"), list):
            raise RuntimeError(f"{source}: service {name}: volumes must be a list")
    if "build" in spec:
        _parse_build(name, spec.get("build"), source=source)
    if "image" in spec:
        _parse_image(name, spec.get("image"), source=source)
    if "environment" in spec:
        _parse_environment(name, spec.get("environment"), source=source)
    if "healthcheck" in spec:
        _parse_healthcheck(name, spec.get("healthcheck"), source=source)
    if "networks" in spec:
        _parse_networks(name, spec.get("networks"), source=source)
    _parse_resources(name, spec, source=source)


def _parse_sidecar(name: str, spec: Any, *, source: str) -> ComposeSidecar:
    if name in RESERVED_SIDECAR_NAMES:
        raise RuntimeError(f"{source}: service name {name!r} is reserved")
    if not _DNS_LABEL_RE.fullmatch(name):
        raise RuntimeError(f"{source}: service name {name!r} is not a DNS-1123 label")
    spec = _require_mapping(name, spec, source=source)

    unknown = sorted(set(spec) - _ALLOWED_SIDECAR_KEYS)
    if unknown:
        raise RuntimeError(f"{source}: service {name}: unsupported keys {unknown}")
    _reject_interpolation(spec, location=f"service {name}", source=source)

    has_image = "image" in spec
    has_build = "build" in spec
    if has_image == has_build:
        raise RuntimeError(f"{source}: service {name}: declare exactly one of 'image' or 'build'")

    dockerfile: str | None = None
    image_ref: str | None = None
    if has_image:
        image_ref = _parse_image(name, spec["image"], source=source)
    else:
        dockerfile = _parse_build(name, spec["build"], source=source)

    memory_request_bytes, memory_limit_bytes, cpu_request = _parse_resources(name, spec, source=source)
    _parse_networks(name, spec.get("networks"), source=source)
    healthcheck = _parse_healthcheck(name, spec.get("healthcheck"), source=source)
    if healthcheck is None:
        raise RuntimeError(f"{source}: service {name}: healthcheck is required")

    tmpfs_mounts = tuple(_parse_volumes(name, spec.get("volumes"), source=source))
    tmpfs_bytes = sum(mount.size_bytes or 0 for mount in tmpfs_mounts)
    if tmpfs_bytes:
        if memory_limit_bytes is None:
            raise RuntimeError(f"{source}: service {name}: tmpfs requires a memory limit")

        if memory_request_bytes is None or memory_request_bytes <= tmpfs_bytes:
            raise RuntimeError(f"{source}: service {name}: memory request must exceed total tmpfs size")

        if memory_limit_bytes <= tmpfs_bytes:
            raise RuntimeError(f"{source}: service {name}: memory limit must exceed total tmpfs size")

    return ComposeSidecar(
        name=name,
        image=image_ref,
        dockerfile=dockerfile,
        env=_parse_environment(name, spec.get("environment"), source=source),
        healthcheck=healthcheck,
        tmpfs_mounts=tmpfs_mounts,
        memory_request_bytes=memory_request_bytes,
        memory_limit_bytes=memory_limit_bytes,
        cpu_request=cpu_request,
    )


def _parse_image(name: str, image: Any, *, source: str) -> str:
    if not isinstance(image, str):
        raise RuntimeError(f"{source}: service {name}: image must be a string")
    if any(character in image for character in "\n\r\0"):
        raise RuntimeError(f"{source}: service {name}: image must be a single line")
    if not image.strip():
        raise RuntimeError(f"{source}: service {name}: image must be a string")
    return image


def _parse_build(name: str, build: Any, *, source: str) -> str:
    if isinstance(build, str):
        raise RuntimeError(f"{source}: service {name}: build must be a mapping with context and dockerfile")
    if not isinstance(build, dict):
        raise RuntimeError(f"{source}: service {name}: build must be a mapping")
    unknown = sorted(set(build) - _ALLOWED_BUILD_KEYS)
    if unknown:
        raise RuntimeError(f"{source}: service {name}: unsupported build keys {unknown}")
    context = build.get("context", ".")
    if context != ".":
        raise RuntimeError(f"{source}: service {name}: build.context must be '.'")
    dockerfile = build.get("dockerfile")
    if not dockerfile or not isinstance(dockerfile, str):
        raise RuntimeError(f"{source}: service {name}: build.dockerfile is required")
    if not _DOCKERFILE_NAME_RE.fullmatch(dockerfile):
        raise RuntimeError(f"{source}: service {name}: build.dockerfile must be a basename")
    return dockerfile


def _parse_environment(name: str, environment: Any, *, source: str) -> dict[str, str]:
    if environment is None:
        return {}
    if isinstance(environment, Mapping):
        inherited = [str(key) for key, value in environment.items() if value is None]
        if inherited:
            raise RuntimeError(f"{source}: service {name}: environment values must be explicit: {sorted(inherited)}")
        if not all(isinstance(key, str) for key in environment):
            raise RuntimeError(f"{source}: service {name}: environment keys must be strings")
        invalid_values = [key for key, value in environment.items() if not isinstance(value, str)]
        if invalid_values:
            raise RuntimeError(
                f"{source}: service {name}: environment values must be strings: {sorted(invalid_values)}"
            )
        return dict(environment)
    if isinstance(environment, list):
        parsed: dict[str, str] = {}
        for index, item in enumerate(environment):
            if not isinstance(item, str) or "=" not in item:
                raise RuntimeError(f"{source}: service {name}: environment[{index}] must be KEY=VALUE")
            key, value = item.split("=", 1)
            parsed[key] = value
        return parsed
    raise RuntimeError(f"{source}: service {name}: environment must be a mapping or list")


def _reject_interpolation(
    value: Any,
    *,
    location: str,
    source: str,
    _active_ids: set[int] | None = None,
) -> None:
    if isinstance(value, str):
        if "$" in value:
            raise RuntimeError(f"{source}: {location} must not use host-environment interpolation")
        return

    if not isinstance(value, (list, Mapping)):
        return

    active_ids = _active_ids if _active_ids is not None else set()
    identity = id(value)
    if identity in active_ids:
        raise RuntimeError(f"{source}: {location} must not contain recursive YAML anchors")
    active_ids.add(identity)
    try:
        if isinstance(value, list):
            for index, item in enumerate(value):
                _reject_interpolation(
                    item,
                    location=f"{location}[{index}]",
                    source=source,
                    _active_ids=active_ids,
                )
        else:
            for key, item in value.items():
                _reject_interpolation(
                    item,
                    location=f"{location}.{key}",
                    source=source,
                    _active_ids=active_ids,
                )
    finally:
        active_ids.remove(identity)


def _parse_networks(name: str, networks: Any, *, source: str) -> None:
    """Shape-check inert Compose networking without coupling to network names."""
    if networks is None:
        return

    if isinstance(networks, list):
        if all(isinstance(network, str) for network in networks):
            return

    elif isinstance(networks, Mapping):
        if all(isinstance(network, str) for network in networks):
            return

    raise RuntimeError(f"{source}: service {name}: networks must be a list of strings or a string-keyed mapping")


def _parse_healthcheck(name: str, healthcheck: Any, *, source: str) -> ComposeHealthcheck | None:
    if healthcheck is None:
        return None
    if not isinstance(healthcheck, dict):
        raise RuntimeError(f"{source}: service {name}: healthcheck must be a mapping")
    unknown = sorted(set(healthcheck) - _ALLOWED_HEALTHCHECK_KEYS)
    if unknown:
        raise RuntimeError(f"{source}: service {name}: unsupported healthcheck keys {unknown}")
    test = healthcheck.get("test")
    if not isinstance(test, list) or len(test) < 2 or test[0] != "CMD-SHELL":
        raise RuntimeError(f"{source}: service {name}: healthcheck.test must be CMD-SHELL plus a string")
    if not all(isinstance(part, str) for part in test[1:]):
        raise RuntimeError(f"{source}: service {name}: healthcheck.test must be CMD-SHELL plus a string")
    script_parts = test[1:]
    retries = healthcheck.get("retries")
    if retries is not None and (not isinstance(retries, int) or retries < 1):
        raise RuntimeError(f"{source}: service {name}: healthcheck.retries must be a positive integer")
    return ComposeHealthcheck(
        test_script=" ".join(script_parts),
        interval=healthcheck.get("interval"),
        timeout=healthcheck.get("timeout"),
        retries=retries,
    )


def _parse_volumes(name: str, volumes: Any, *, source: str) -> list[TmpfsMount]:
    if volumes is None:
        return []
    if not isinstance(volumes, list):
        raise RuntimeError(f"{source}: service {name}: volumes must be a list")
    mounts: list[TmpfsMount] = []
    for index, volume in enumerate(volumes):
        if not isinstance(volume, Mapping):
            raise RuntimeError(f"{source}: service {name}: volume {index} must be a mapping")
        volume_type = volume.get("type")
        if volume_type != "tmpfs":
            raise RuntimeError(f"{source}: service {name}: volume {index} must have type tmpfs")
        target = volume.get("target")
        if not isinstance(target, str) or not target.startswith("/"):
            raise RuntimeError(f"{source}: service {name}: volume {index} target must be an absolute path")
        extra = set(volume) - {"type", "target", "tmpfs"}
        if extra:
            raise RuntimeError(f"{source}: service {name}: volume {index} has unsupported keys {sorted(extra)}")
        tmpfs = volume.get("tmpfs")
        if not isinstance(tmpfs, Mapping) or set(tmpfs) != {"size"}:
            raise RuntimeError(f"{source}: service {name}: volume {index} tmpfs must contain exactly one size")
        size = parse_compose_bytes(
            tmpfs["size"],
            source=source,
            field=f"service {name}: volume {index} tmpfs.size",
        )
        mounts.append(TmpfsMount(target=target, size_bytes=size))
    return mounts


def _parse_resources(name: str, spec: Mapping[str, Any], *, source: str) -> tuple[int | None, int | None, str | None]:
    if "deploy" in spec and ("mem_limit" in spec or "mem_reservation" in spec):
        raise RuntimeError(f"{source}: service {name}: must not mix deploy resources with legacy memory fields")

    deploy_request, deploy_limit, cpu_request = _parse_deploy(name, spec.get("deploy"), source=source)
    legacy_request = None
    if "mem_reservation" in spec and deploy_request is None:
        legacy_request = parse_compose_bytes(
            spec["mem_reservation"], source=source, field=f"service {name}: mem_reservation"
        )
    legacy_limit = None
    if "mem_limit" in spec and deploy_limit is None:
        legacy_limit = parse_compose_bytes(spec["mem_limit"], source=source, field=f"service {name}: mem_limit")

    memory_request = deploy_request if deploy_request is not None else legacy_request
    memory_limit = deploy_limit if deploy_limit is not None else legacy_limit
    if memory_request is None and memory_limit is not None:
        memory_request = memory_limit
    if memory_request is not None and memory_limit is not None and memory_request > memory_limit:
        raise RuntimeError(f"{source}: service {name}: memory request exceeds memory limit")
    return memory_request, memory_limit, cpu_request


def _parse_deploy(name: str, deploy: Any, *, source: str) -> tuple[int | None, int | None, str | None]:
    if deploy is None:
        return None, None, None
    if not isinstance(deploy, Mapping):
        raise RuntimeError(f"{source}: service {name}: deploy must be a mapping")
    unknown = sorted(set(deploy) - _ALLOWED_DEPLOY_KEYS)
    if unknown:
        raise RuntimeError(f"{source}: service {name}: unsupported deploy keys {unknown}")
    resources = deploy.get("resources")
    if resources is None:
        return None, None, None
    if not isinstance(resources, Mapping):
        raise RuntimeError(f"{source}: service {name}: deploy.resources must be a mapping")
    unknown_resources = sorted(set(resources) - _ALLOWED_RESOURCES_KEYS)
    if unknown_resources:
        raise RuntimeError(f"{source}: service {name}: unsupported deploy.resources keys {unknown_resources}")

    limits = resources.get("limits")
    reservations = resources.get("reservations")
    memory_limit = None
    if limits is not None:
        if not isinstance(limits, Mapping):
            raise RuntimeError(f"{source}: service {name}: deploy.resources.limits must be a mapping")
        unknown_limits = sorted(set(limits) - _ALLOWED_LIMITS_KEYS)
        if unknown_limits:
            raise RuntimeError(f"{source}: service {name}: unsupported deploy.resources.limits keys {unknown_limits}")
        if "memory" in limits:
            memory_limit = parse_compose_bytes(
                limits["memory"], source=source, field=f"service {name}: deploy.resources.limits.memory"
            )

    memory_request = None
    cpu_request = None
    if reservations is not None:
        if not isinstance(reservations, Mapping):
            raise RuntimeError(f"{source}: service {name}: deploy.resources.reservations must be a mapping")
        unknown_reservations = sorted(set(reservations) - _ALLOWED_RESERVATIONS_KEYS)
        if unknown_reservations:
            raise RuntimeError(
                f"{source}: service {name}: unsupported deploy.resources.reservations keys {unknown_reservations}"
            )
        if "memory" in reservations:
            memory_request = parse_compose_bytes(
                reservations["memory"],
                source=source,
                field=f"service {name}: deploy.resources.reservations.memory",
            )
        if "cpus" in reservations:
            cpu_request = parse_compose_cpus(
                reservations["cpus"],
                source=source,
                field=f"service {name}: deploy.resources.reservations.cpus",
            )
    return memory_request, memory_limit, cpu_request
