import os
from types import SimpleNamespace
from uuid import uuid4

import docker
import pytest
from docker.errors import NotFound
from docker.types import Mount

import utils.docker as docker_utils

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DOCKER_JANITOR_INTEGRATION") != "1",
    reason="set RUN_DOCKER_JANITOR_INTEGRATION=1 to run scoped Docker volume tests",
)


@pytest.fixture
def volume_fixture(monkeypatch):
    image = os.environ["JANITOR_TEST_IMAGE"]
    client = docker.from_env(timeout=30)
    containers = []
    volume_names = set()
    suffix = uuid4().hex
    project = f"janitor-volume-{suffix[:12]}__{suffix[12:19]}__verifier__trial"

    def track(container):
        containers.append(container)
        container.reload()
        volume_names.update(m["Name"] for m in container.attrs["Mounts"] if m["Type"] == "volume")
        return container

    try:
        # Fail rather than silently pull an image during this cleanup test.
        client.images.get(image)
        named = client.volumes.create(name=f"janitor-volume-review-{suffix}-named")
        volume_names.add(named.name)
        candidate = track(
            client.containers.create(
                image,
                name=f"{project}-clickhouse-init-1",
                labels={
                    docker_utils.COMPOSE_PROJECT_LABEL: project,
                    docker_utils.COMPOSE_SERVICE_LABEL: "clickhouse-init",
                },
                mounts=[
                    Mount(target="/janitor-private", source="", type="volume"),
                    Mount(target="/janitor-shared", source="", type="volume"),
                    Mount(target="/janitor-named", source=named.name, type="volume"),
                ],
            )
        )
        mounts = {m["Destination"]: m["Name"] for m in candidate.attrs["Mounts"] if m["Type"] == "volume"}
        shared_name = mounts["/janitor-shared"]
        # Include anonymous volumes inherited from the image's VOLUME entries,
        # which are the original DB-sidecar leak, as well as our explicit mount.
        private_names = set(mounts.values()) - {shared_name, named.name}
        holder = track(
            client.containers.create(
                image,
                name=f"janitor-volume-holder-{suffix}",
                mounts=[Mount(target="/held", source=shared_name, type="volume")],
            )
        )
        allowed_ids = {c.id for c in containers}

        def get_container(container_id):
            assert container_id in allowed_ids, "Janitor requested a container outside the test inventory"
            return client.containers.get(container_id)

        scoped_client = SimpleNamespace(
            api=SimpleNamespace(
                containers=lambda all: [{"Id": c.id, "Names": [f"/{c.name}"]} for c in containers],
            ),
            containers=SimpleNamespace(get=get_container),
            networks=SimpleNamespace(list=lambda: []),
            images=client.images,
        )
        monkeypatch.setattr(docker_utils, "docker_client", scoped_client)
        yield SimpleNamespace(
            client=client,
            candidate=candidate,
            holder=holder,
            private_names=private_names,
            shared_name=shared_name,
            named_name=named.name,
        )
    finally:
        # Exact objects created above only. Never prune or force-remove volumes.
        cleanup_errors = []
        try:
            for container in reversed(containers):
                try:
                    container.remove(force=True, v=True)
                except NotFound:
                    pass
                except docker.errors.APIError as exc:
                    cleanup_errors.append(exc)
            for name in volume_names:
                try:
                    client.volumes.get(name).remove(force=False)
                except NotFound:
                    pass
                except docker.errors.APIError as exc:
                    cleanup_errors.append(exc)
        finally:
            client.close()
        if cleanup_errors:
            raise ExceptionGroup("Failed to clean up Docker volume test resources", cleanup_errors)


@pytest.mark.parametrize("startup", [True, False])
@pytest.mark.parametrize("dry_run", [True, False])
def test_janitor_removes_only_unshared_anonymous_volumes(volume_fixture, startup, dry_run):
    fixture = volume_fixture
    if startup:
        result = docker_utils.cleanup_harbor_docker_resources(dry_run=dry_run)
    else:
        # The fixture is freshly created: make it eligible without waiting.
        # This applies only to this invocation's two-container test inventory.
        result = docker_utils.sweep_stale_harbor_containers(stopped_grace_sec=0, running_ttl_sec=0, dry_run=dry_run)

    assert result["count"] == 1
    assert result["errors"] == 0
    if dry_run:
        fixture.client.containers.get(fixture.candidate.id)
        for name in fixture.private_names:
            fixture.client.volumes.get(name)
    else:
        with pytest.raises(NotFound):
            fixture.client.containers.get(fixture.candidate.id)
        for name in fixture.private_names:
            with pytest.raises(NotFound):
                fixture.client.volumes.get(name)

    # A stopped/created foreign container must protect its shared volume too.
    fixture.client.containers.get(fixture.holder.id)
    fixture.client.volumes.get(fixture.shared_name)
    # Explicitly named data is retained even after its only container is gone.
    fixture.client.volumes.get(fixture.named_name)
