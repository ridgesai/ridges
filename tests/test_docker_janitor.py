from datetime import datetime, timedelta, timezone

import pytest
from docker.errors import NotFound as docker_errors_NotFound

import utils.docker as docker_utils
from utils.docker import (
    _parse_docker_time,
    cleanup_harbor_docker_resources,
    image_ref_is_janitor_target,
    is_janitor_container,
    prune_caches_under_disk_pressure,
    prune_docker_disk_resources,
    sweep_leaked_harbor_images,
    sweep_stale_harbor_containers,
)

TRIAL_LABEL = docker_utils.RIDGES_TRIAL_ID_LABEL


def _iso(age: timedelta) -> str:
    return (datetime.now(timezone.utc) - age).isoformat()


class FakeContainer:
    def __init__(
        self,
        name,
        status,
        *,
        labeled=True,
        age=timedelta(hours=2),
        image_id="sha256:aaa",
        image_ref=None,
        restart_policy="no",
    ):
        self.id = f"id-{name}"
        self.name = name
        self.status = status
        self.labels = {TRIAL_LABEL: "t123"} if labeled else {}
        self.attrs = {
            "Created": _iso(age),
            "ImageID": image_id,
            "Image": image_id,
            "Config": {"Image": image_ref} if image_ref else {},
            "HostConfig": {"RestartPolicy": {"Name": restart_policy}} if restart_policy is not None else {},
        }
        self.removed = False

    def reload(self):
        pass

    def remove(self, force=False):
        self.removed = True

    def summary(self):
        """The bulk /containers/json representation of this container."""
        names = self.summary_names if getattr(self, "summary_names", None) else [f"/{self.name}"]
        return {
            "Id": self.id,
            "Names": names,
            "ImageID": self.attrs.get("ImageID"),
            "Image": self.attrs.get("Image"),
            "Labels": self.labels,
        }


class FakeNetwork:
    def __init__(self, name, *, age=timedelta(hours=2), attached=False, compose_network="sandbox_egress"):
        self.name = name
        labels = {"com.docker.compose.network": compose_network} if compose_network else {}
        self.attrs = {
            "Created": _iso(age),
            "Containers": {"c1": {}} if attached else {},
            "Labels": labels,
        }
        self.removed = False

    def reload(self):
        pass

    def remove(self):
        self.removed = True


class FakeImage:
    def __init__(self, image_id, tags, *, last_tag_age=timedelta(hours=12), size=1_000_000_000):
        self.id = image_id
        self.tags = tags
        metadata = {"LastTagTime": _iso(last_tag_age) if last_tag_age is not None else "0001-01-01T00:00:00Z"}
        self.attrs = {
            "Metadata": metadata,
            "RepoTags": list(tags) if tags is not None else None,
            "Size": size,
        }


class FakeClient:
    def __init__(
        self,
        containers=None,
        networks=None,
        images=None,
        *,
        vanished_container_ids=(),
        vanished_image_ids=(),
        image_prune_reclaimed=None,
    ):
        self._containers = containers or []
        self._networks = networks or []
        self._images = images or []
        self.vanished_container_ids = set(vanished_container_ids)
        self.vanished_image_ids = set(vanished_image_ids)
        self.removed_tags = []
        self.pruned_images = False
        self.image_prune_filters = []
        self.image_prune_reclaimed = list(image_prune_reclaimed or [])
        self.pruned_builds = False
        self.build_prune_calls = []

        client = self

        class Containers:
            def list(self, all=False, filters=None):
                return list(client._containers)

            def get(self, container_id):
                if container_id in client.vanished_container_ids:
                    raise docker_errors_NotFound(f"No such container: {container_id}")
                for c in client._containers:
                    if c.id == container_id or c.name == container_id:
                        return c
                raise docker_errors_NotFound(f"No such container: {container_id}")

        class Networks:
            def list(self):
                return list(client._networks)

        class Images:
            def get(self, image_id):
                if image_id in client.vanished_image_ids:
                    raise docker_errors_NotFound(f"No such image: {image_id}")
                for i in client._images:
                    if i.id == image_id or image_id in (i.tags or []):
                        return i
                raise docker_errors_NotFound(f"No such image: {image_id}")

            def remove(self, tag, force=False, noprune=False):
                assert force is False and noprune is True
                client.removed_tags.append(tag)

            def prune(self, filters=None):
                client.pruned_images = True
                client.image_prune_filters.append(filters)
                reclaimed = client.image_prune_reclaimed.pop(0) if client.image_prune_reclaimed else 123
                return {"SpaceReclaimed": reclaimed}

        class Api:
            def containers(self, all=False, filters=None):
                result = client._containers
                if filters and "label" in filters:
                    result = [c for c in result if filters["label"] in c.labels]
                return [c.summary() for c in result]

            def images(self):
                return [{"Id": i.id, "RepoTags": list(i.tags) if i.tags is not None else None} for i in client._images]

            def prune_builds(self, filters=None, all=None):
                client.pruned_builds = True
                client.build_prune_calls.append({"filters": filters, "all": all})
                return {"SpaceReclaimed": 456}

        self.containers = Containers()
        self.networks = Networks()
        self.images = Images()
        self.api = Api()


@pytest.fixture
def inject_client(monkeypatch):
    def _inject(client):
        monkeypatch.setattr(docker_utils, "docker_client", client)
        monkeypatch.setattr(docker_utils, "docker_client_long_timeout", client)
        return client

    return _inject


# --- _parse_docker_time -----------------------------------------------------


def test_parse_docker_time_handles_nano_z_zero_and_garbage():
    assert _parse_docker_time("2026-07-26T21:45:00.123456789Z") is not None
    assert _parse_docker_time("2026-07-26T21:45:00+02:00") is not None
    assert _parse_docker_time("0001-01-01T00:00:00Z") is None
    assert _parse_docker_time("not-a-date") is None
    assert _parse_docker_time(None) is None


# --- janitor provenance -----------------------------------------------------


def test_image_allowlist_matches_only_eval_images():
    assert image_ref_is_janitor_target("django__django-15128__zjr4wmw-sandbox-proxy:latest")
    assert image_ref_is_janitor_target("polyglot_python_go-counting__fkwuwgj-main:latest")
    assert image_ref_is_janitor_target("task__x__abc-main:latest")
    assert image_ref_is_janitor_target("swebench/sweb.eval.x86_64.django_1776_django-15128:latest")
    assert image_ref_is_janitor_target("jefzda/sweap-images:ansible.ansible-abc")
    assert not image_ref_is_janitor_target("ubuntu:24.04")
    assert not image_ref_is_janitor_target("python:3.12-slim")
    assert not image_ref_is_janitor_target("buildpack-deps:jammy")
    assert not image_ref_is_janitor_target("ghcr.io/ridgesai/sandbox-proxy:0.0.1")
    assert not image_ref_is_janitor_target("ghcr.io/foreign/task__x__abc-main:latest")
    assert not image_ref_is_janitor_target("sha256:deadbeef")
    assert not image_ref_is_janitor_target("swebench/sweb.eval.foo@sha256:deadbeef")
    assert not image_ref_is_janitor_target("registry:5000/task__x__abc-main:latest")
    assert not image_ref_is_janitor_target(None)


FULL_ID = "sha256:" + "9b" * 32


def test_autogen_container_requires_eval_provenance():
    client = FakeClient()
    eval_leftover = FakeContainer(
        "condescending_ramanujan",
        "exited",
        labeled=False,
        image_ref="swebench/sweb.eval.x86_64.django_1776_django-15128:latest",
    )
    postgres = FakeContainer("pedantic_hopper", "exited", labeled=False, image_ref="postgres:16")
    exporter = FakeContainer(
        "node_exporter",
        "running",
        labeled=False,
        image_ref="prom/node-exporter:latest",
        restart_policy="unless-stopped",
    )
    restartable = FakeContainer(
        "gallant_solomon",
        "running",
        labeled=False,
        image_ref="swebench/sweb.eval.x86_64.django_1776_django-15128:latest",
        restart_policy="always",
    )
    missing_policy = FakeContainer(
        "boring_wozniak",
        "exited",
        labeled=False,
        image_ref="swebench/sweb.eval.x86_64.django_1776_django-15128:latest",
        restart_policy=None,
    )
    unknown = FakeContainer("nifty_shamir", "exited", labeled=False)
    harbor = FakeContainer("task__x__abc-main-1", "exited", labeled=False)

    assert is_janitor_container(eval_leftover, client)
    assert is_janitor_container(harbor, client)
    assert not is_janitor_container(postgres, client)
    assert not is_janitor_container(exporter, client)
    assert not is_janitor_container(restartable, client)
    assert not is_janitor_container(missing_policy, client)
    assert not is_janitor_container(unknown, client)


def test_autogen_untagged_intermediate_requires_full_resolvable_image_id():
    intermediate = FakeImage(FULL_ID, [])
    tagged = FakeImage("sha256:" + "ab" * 32, ["myapp:v1"])
    client = FakeClient(images=[intermediate, tagged])
    orphan = FakeContainer("gallant_khayyam", "running", labeled=False, image_id=FULL_ID, image_ref=FULL_ID)
    tagged_by_id = FakeContainer("vigorous_murdock", "running", labeled=False, image_id=tagged.id, image_ref=tagged.id)
    truncated = FakeContainer("clever_mccarthy", "running", labeled=False, image_ref="9bd5ba7bca93")
    unresolvable_id = "sha256:" + "ff" * 32
    unresolvable = FakeContainer(
        "priceless_haslett",
        "running",
        labeled=False,
        image_id=unresolvable_id,
        image_ref=unresolvable_id,
    )

    assert is_janitor_container(orphan, client)
    assert not is_janitor_container(tagged_by_id, client)
    assert not is_janitor_container(truncated, client)
    assert not is_janitor_container(unresolvable, client)


# --- container sweep --------------------------------------------------------


def _sweep_containers(dry_run=False):
    return sweep_stale_harbor_containers(stopped_grace_sec=45 * 60, running_ttl_sec=6 * 3600, dry_run=dry_run)


def test_old_stopped_corpses_removed_fresh_and_running_kept(inject_client):
    old_exited = FakeContainer("task__x__abc-main-1", "exited", age=timedelta(hours=2))
    old_created = FakeContainer("task__x__def-sandbox-proxy-1", "created", age=timedelta(hours=2))
    fresh_exited = FakeContainer("task__x__ghi-main-1", "exited", age=timedelta(minutes=5))
    running = FakeContainer("task__x__jkl-main-1", "running", age=timedelta(hours=1))
    inject_client(FakeClient(containers=[old_exited, old_created, fresh_exited, running]))

    removed = _sweep_containers()

    assert removed["count"] == 2
    assert removed["names"] == ["task__x__abc-main-1", "task__x__def-sandbox-proxy-1"]
    assert old_exited.removed and old_created.removed
    assert not fresh_exited.removed and not running.removed


def test_orphaned_running_container_removed_after_ttl(inject_client):
    orphan = FakeContainer("task__x__abc-main-1", "running", age=timedelta(hours=7))
    inject_client(FakeClient(containers=[orphan]))

    assert _sweep_containers()["count"] == 1
    assert orphan.removed


def test_harbor_name_is_provenance_but_unmatched_name_is_kept(inject_client):
    unlabeled = FakeContainer("task__x__abc-main-1", "exited", labeled=False, age=timedelta(hours=9))
    wrong_name = FakeContainer("my-postgres", "exited", age=timedelta(hours=9))
    inject_client(FakeClient(containers=[unlabeled, wrong_name]))

    assert _sweep_containers()["count"] == 1
    assert unlabeled.removed and not wrong_name.removed


def test_unlabeled_autogen_eval_and_classic_builder_orphans_are_removed(inject_client):
    eval_leftover = FakeContainer(
        "condescending_ramanujan",
        "exited",
        labeled=False,
        age=timedelta(hours=2),
        image_ref="swebench/sweb.eval.x86_64.django_1776_django-15128:latest",
    )
    intermediate = FakeImage(FULL_ID, [])
    build_orphan = FakeContainer(
        "gallant_khayyam",
        "running",
        labeled=False,
        age=timedelta(hours=7),
        image_id=FULL_ID,
        image_ref=FULL_ID,
    )
    client = inject_client(FakeClient(containers=[eval_leftover, build_orphan], images=[intermediate]))

    result = _sweep_containers()

    assert result["count"] == 2
    assert eval_leftover.removed and build_orphan.removed
    assert client._images == [intermediate]


def test_dry_run_counts_but_does_not_remove(inject_client):
    corpse = FakeContainer("task__x__abc-main-1", "exited", age=timedelta(hours=2))
    inject_client(FakeClient(containers=[corpse]))

    assert _sweep_containers(dry_run=True)["count"] == 1
    assert not corpse.removed


def test_detached_old_harbor_networks_removed_attached_kept(inject_client):
    detached = FakeNetwork("task__x__abc_sandbox_egress", age=timedelta(hours=2))
    attached = FakeNetwork("task__x__def_sandbox_internal", age=timedelta(hours=2), attached=True)
    other = FakeNetwork("bridge")
    inject_client(FakeClient(networks=[detached, attached, other]))

    _sweep_containers()

    assert detached.removed
    assert not attached.removed and not other.removed


def test_network_without_compose_label_never_removed(inject_client):
    """Provenance guard: a hand-made network that merely matches the name pattern survives."""
    impostor = FakeNetwork("ops__foo_sandbox_internal", age=timedelta(days=3), compose_network=None)
    inject_client(FakeClient(networks=[impostor]))

    _sweep_containers()

    assert not impostor.removed


def test_network_dry_run_does_not_remove(inject_client):
    stale = FakeNetwork("task__x__abc_sandbox_egress", age=timedelta(hours=2))
    inject_client(FakeClient(networks=[stale]))

    _sweep_containers(dry_run=True)

    assert not stale.removed


def test_stopped_corpse_that_restarts_during_reload_is_kept(inject_client):
    """Reload flips exited -> running (fresh): second _qualifies check must veto removal."""

    class RestartingContainer(FakeContainer):
        def reload(self):
            self.status = "running"

    revived = RestartingContainer("task__x__abc-main-1", "exited", age=timedelta(hours=2))
    inject_client(FakeClient(containers=[revived]))

    assert _sweep_containers()["count"] == 0
    assert not revived.removed


def test_stopped_corpse_removed_without_force(inject_client):
    """Stopped corpses use force=False so docker itself rejects a last-instant restart."""
    corpse = FakeContainer("task__x__abc-main-1", "exited", age=timedelta(hours=2))
    seen_force = []
    original_remove = corpse.remove

    def recording_remove(force=False):
        seen_force.append(force)
        original_remove(force=force)

    corpse.remove = recording_remove
    orphan = FakeContainer("task__x__def-main-1", "running", age=timedelta(hours=9))
    orphan_force = []
    orphan_original = orphan.remove

    def orphan_remove(force=False):
        orphan_force.append(force)
        orphan_original(force=force)

    orphan.remove = orphan_remove
    inject_client(FakeClient(containers=[corpse, orphan]))

    assert _sweep_containers()["count"] == 2
    assert seen_force == [False]
    assert orphan_force == [True]


def test_remove_failure_does_not_abort_sweep(inject_client):
    """One refused removal (e.g. race) must not stop the rest of the sweep."""
    flaky = FakeContainer("task__x__abc-main-1", "exited", age=timedelta(hours=2))

    def failing_remove(force=False):
        raise RuntimeError("conflict: container is running")

    flaky.remove = failing_remove
    other = FakeContainer("task__x__def-main-1", "exited", age=timedelta(hours=2))
    inject_client(FakeClient(containers=[flaky, other]))

    result = _sweep_containers()

    assert result["count"] == 1
    assert result["errors"] == 1
    assert other.removed


def test_missing_created_timestamp_is_never_removed(inject_client):
    corpse = FakeContainer("task__x__abc-main-1", "exited", age=timedelta(hours=2))
    corpse.attrs["Created"] = None
    inject_client(FakeClient(containers=[corpse]))

    assert _sweep_containers()["count"] == 0
    assert not corpse.removed


# --- startup cleanup --------------------------------------------------------


def test_startup_removes_harbor_and_autogen_eval_but_keeps_generic_container(inject_client):
    harbor = FakeContainer("task__x__abc-main-1", "running", labeled=False, age=timedelta(minutes=5))
    eval_leftover = FakeContainer(
        "condescending_ramanujan",
        "created",
        labeled=False,
        age=timedelta(minutes=5),
        image_ref="swebench/sweb.eval.x86_64.django_1776_django-15128:latest",
    )
    postgres = FakeContainer(
        "pedantic_hopper", "running", labeled=False, age=timedelta(days=2), image_ref="postgres:16"
    )
    inject_client(FakeClient(containers=[harbor, eval_leftover, postgres]))

    result = cleanup_harbor_docker_resources(dry_run=False)

    assert result["count"] == 2
    assert harbor.removed and eval_leftover.removed
    assert not postgres.removed


def test_startup_keeps_fresh_classic_step_but_removes_expired_one(inject_client):
    intermediate = FakeImage(FULL_ID, [])
    fresh = FakeContainer(
        "fresh_builder",
        "running",
        labeled=False,
        age=timedelta(minutes=10),
        image_id=FULL_ID,
        image_ref=FULL_ID,
    )
    expired = FakeContainer(
        "expired_builder",
        "running",
        labeled=False,
        age=timedelta(days=2),
        image_id=FULL_ID,
        image_ref=FULL_ID,
    )
    inject_client(FakeClient(containers=[fresh, expired], images=[intermediate]))

    result = cleanup_harbor_docker_resources(
        dry_run=False,
        stopped_grace_sec=45 * 60,
        running_ttl_sec=6 * 3600,
    )

    assert result["count"] == 1
    assert not fresh.removed and expired.removed


def test_startup_dry_run_reports_without_removing(inject_client):
    harbor = FakeContainer("task__x__abc-main-1", "running", labeled=False, age=timedelta(days=2))
    network = FakeNetwork("task__x__abc_sandbox_egress", age=timedelta(days=2))
    inject_client(FakeClient(containers=[harbor], networks=[network]))

    result = cleanup_harbor_docker_resources(dry_run=True)

    assert result == {"count": 1, "names": ["task__x__abc-main-1"], "errors": 0}
    assert not harbor.removed and not network.removed


# --- image sweep ------------------------------------------------------------


def _sweep_images(dry_run=False, disk_used_percent=65.0):
    return sweep_leaked_harbor_images(
        tag_grace_sec=6 * 3600,
        dry_run=dry_run,
        disk_used_percent=disk_used_percent,
    )


def test_unreferenced_old_harbor_image_removed_by_tag(inject_client):
    leaked = FakeImage("sha256:leak", ["task__x__abc-main:latest"], last_tag_age=timedelta(hours=12))
    client = inject_client(FakeClient(images=[leaked]))

    assert _sweep_images()["count"] == 1
    assert client.removed_tags == ["task__x__abc-main:latest"]


def test_referenced_image_kept(inject_client):
    referenced = FakeImage("sha256:ref", ["task__x__abc-main:latest"], last_tag_age=timedelta(hours=12))
    holder = FakeContainer("task__x__abc-main-1", "exited", image_id="sha256:ref")
    client = inject_client(FakeClient(containers=[holder], images=[referenced]))

    assert _sweep_images()["count"] == 0
    assert client.removed_tags == []


def test_base_and_non_harbor_images_never_match(inject_client):
    base = FakeImage("sha256:base", ["ubuntu:24.04"], last_tag_age=timedelta(days=30))
    proxy = FakeImage("sha256:proxy", ["ghcr.io/ridgesai/sandbox-proxy:0.0.1"], last_tag_age=timedelta(days=30))
    client = inject_client(FakeClient(images=[base, proxy]))

    assert _sweep_images()["count"] == 0
    assert client.removed_tags == []


def test_pulled_eval_images_with_zero_lasttagtime_removed_above_pressure(inject_client):
    sweap = FakeImage("sha256:sweap", ["jefzda/sweap-images:ansible.ansible-abc"], last_tag_age=None)
    sweb = FakeImage(
        "sha256:sweb",
        ["swebench/sweb.eval.x86_64.django_1776_django-15128:latest"],
        last_tag_age=None,
    )
    client = inject_client(FakeClient(images=[sweap, sweb]))

    result = _sweep_images(disk_used_percent=50.0)

    assert result["count"] == 2
    assert result["names"] == [
        "jefzda/sweap-images:ansible.ansible-abc",
        "swebench/sweb.eval.x86_64.django_1776_django-15128:latest",
    ]
    assert client.removed_tags == result["names"]


@pytest.mark.parametrize("disk_used_percent", [49.9, None])
def test_pulled_eval_images_kept_without_disk_pressure(inject_client, disk_used_percent):
    pulled = FakeImage(
        "sha256:sweb",
        ["swebench/sweb.eval.x86_64.django_1776_django-15128:latest"],
        last_tag_age=None,
    )
    client = inject_client(FakeClient(images=[pulled]))

    assert _sweep_images(disk_used_percent=disk_used_percent)["count"] == 0
    assert client.removed_tags == []


def test_zero_or_fresh_last_tag_time_kept(inject_client):
    zero = FakeImage("sha256:zero", ["task__x__abc-main:latest"], last_tag_age=None)
    fresh = FakeImage("sha256:fresh", ["task__x__def-main:latest"], last_tag_age=timedelta(minutes=10))
    client = inject_client(FakeClient(images=[zero, fresh]))

    assert _sweep_images()["count"] == 0
    assert client.removed_tags == []


def test_future_last_tag_time_kept(inject_client):
    future = FakeImage("sha256:future", ["task__x__abc-main:latest"], last_tag_age=timedelta(hours=-2))
    client = inject_client(FakeClient(images=[future]))

    assert _sweep_images()["count"] == 0
    assert client.removed_tags == []


def test_reference_via_inspect_image_key_protects(inject_client):
    """Bulk summaries carry an 'Image' reference field alongside 'ImageID';
    either key protecting a referenced image keeps the belt-and-braces guard."""
    referenced = FakeImage("sha256:ref", ["task__x__abc-main:latest"], last_tag_age=timedelta(hours=12))
    holder = FakeContainer("task__x__abc-main-1", "exited")
    holder.attrs = {"Created": holder.attrs["Created"], "Image": "sha256:ref"}
    client = inject_client(FakeClient(containers=[holder], images=[referenced]))

    assert _sweep_images()["count"] == 0
    assert client.removed_tags == []


def test_image_dry_run_counts_without_removing(inject_client):
    leaked = FakeImage("sha256:leak", ["task__x__abc-main:latest"], last_tag_age=timedelta(hours=12))
    client = inject_client(FakeClient(images=[leaked]))

    assert _sweep_images(dry_run=True)["count"] == 1
    assert client.removed_tags == []


def test_image_sweep_survives_image_vanishing_mid_sweep(inject_client):
    """An image deleted between the bulk listing and its inspect must not abort the sweep."""
    ghost = FakeImage("sha256:ghost", ["task__x__ghost-main:latest"], last_tag_age=timedelta(hours=12))
    leaked = FakeImage("sha256:leak", ["task__x__abc-main:latest"], last_tag_age=timedelta(hours=12))
    client = inject_client(FakeClient(images=[ghost, leaked], vanished_image_ids={"sha256:ghost"}))

    assert _sweep_images()["count"] == 1
    assert client.removed_tags == ["task__x__abc-main:latest"]


def test_image_remove_failure_is_reported(inject_client):
    leaked = FakeImage("sha256:leak", ["task__x__abc-main:latest"], last_tag_age=timedelta(hours=12))
    client = inject_client(FakeClient(images=[leaked]))

    def failing_remove(*_args, **_kwargs):
        raise RuntimeError("daemon refused image removal")

    client.images.remove = failing_remove

    result = _sweep_images()

    assert result["count"] == 0
    assert result["errors"] == 1


def test_image_reference_created_between_scan_and_delete_vetoes_removal(inject_client):
    """The final container scan closes the Harbor-start versus rmi race."""
    image = FakeImage("sha256:race", ["task__x__abc-main:latest"], last_tag_age=timedelta(hours=12))
    holder = FakeContainer("task__x__abc-main-1", "running", image_id=image.id)
    client = inject_client(FakeClient(images=[image]))
    scans = 0

    def containers(all=False, filters=None):
        nonlocal scans
        scans += 1
        return [] if scans == 1 else [holder.summary()]

    client.api.containers = containers

    assert _sweep_images()["count"] == 0
    assert scans >= 2
    assert client.removed_tags == []


def test_container_sweep_survives_container_vanishing_mid_sweep(inject_client):
    """A container deleted between the bulk listing and its inspect must not abort the sweep."""
    ghost = FakeContainer("task__x__ghost-main-1", "exited")
    stale = FakeContainer("task__x__abc-main-1", "exited")
    inject_client(FakeClient(containers=[ghost, stale], vanished_container_ids={ghost.id}))

    assert _sweep_containers()["count"] == 1
    assert stale.removed and not ghost.removed


# --- pressure-gated cache prune ---------------------------------------------


def test_cache_prune_only_fires_at_or_above_threshold(inject_client):
    client = inject_client(FakeClient())

    prune_caches_under_disk_pressure(disk_used_percent=50, pressure_percent=80, dry_run=False)
    assert not client.pruned_images and not client.pruned_builds

    prune_caches_under_disk_pressure(disk_used_percent=80, pressure_percent=80, dry_run=False)
    assert client.pruned_images and client.pruned_builds
    assert client.build_prune_calls == [{"filters": {"until": "1h"}, "all": True}]


def test_cache_prune_dry_run_never_prunes(inject_client):
    client = inject_client(FakeClient())

    prune_caches_under_disk_pressure(disk_used_percent=95, pressure_percent=80, dry_run=True)
    assert not client.pruned_images and not client.pruned_builds


def test_cache_prune_returns_reclaim_summary(inject_client):
    inject_client(FakeClient())

    below = prune_caches_under_disk_pressure(disk_used_percent=50, pressure_percent=80, dry_run=False)
    assert below == {"fired": False, "image_bytes": 0, "build_bytes": 0, "errors": 0}

    fired = prune_caches_under_disk_pressure(disk_used_percent=95, pressure_percent=80, dry_run=False)
    assert fired == {"fired": True, "image_bytes": 123, "build_bytes": 456, "errors": 0}


def test_dangling_prune_repeats_until_zero_and_sums_reclaimed_bytes(inject_client):
    client = inject_client(FakeClient(image_prune_reclaimed=[100, 20, 0]))

    result = prune_docker_disk_resources(include_build_cache=True, dry_run=False, until="1h")

    assert result == {"image_bytes": 120, "build_bytes": 456, "errors": 0}
    assert client.image_prune_filters == [{"dangling": True, "until": "1h"}] * 3
    assert client.build_prune_calls == [{"filters": {"until": "1h"}, "all": True}]
    assert client.pruned_builds


def test_dangling_prune_is_bounded_and_dry_run_is_non_destructive(inject_client):
    client = inject_client(FakeClient(image_prune_reclaimed=[10, 10, 10, 10, 10]))

    result = prune_docker_disk_resources(include_build_cache=False, dry_run=False, until="6h")

    assert result == {"image_bytes": 40, "build_bytes": 0, "errors": 0}
    assert len(client.image_prune_filters) == 4

    dry_client = inject_client(FakeClient())
    assert prune_docker_disk_resources(include_build_cache=True, dry_run=True) == {
        "image_bytes": 0,
        "build_bytes": 0,
        "errors": 0,
    }
    assert not dry_client.pruned_images and not dry_client.pruned_builds


def test_prune_errors_are_returned_to_caller(inject_client):
    client = inject_client(FakeClient())

    def fail_image_prune(filters=None):
        raise RuntimeError("image prune failed")

    def fail_build_prune(filters=None, all=None):
        raise RuntimeError("build prune failed")

    client.images.prune = fail_image_prune
    client.api.prune_builds = fail_build_prune

    result = prune_docker_disk_resources(include_build_cache=True, dry_run=False, until="1h")

    assert result == {"image_bytes": 0, "build_bytes": 0, "errors": 2}


def test_long_timeout_client_fallback_is_not_cached(monkeypatch):
    """A transient init failure must not pin the short-timeout default client forever."""
    default_client = object()
    monkeypatch.setattr(docker_utils, "docker_client", default_client)
    monkeypatch.setattr(docker_utils, "docker_client_long_timeout", None)

    def failing_from_env(timeout=None):
        raise RuntimeError("daemon hiccup")

    monkeypatch.setattr(docker_utils.docker, "from_env", failing_from_env)
    assert docker_utils.get_long_timeout_docker_client() is default_client
    assert docker_utils.docker_client_long_timeout is None  # fallback not cached

    long_client = object()
    monkeypatch.setattr(docker_utils.docker, "from_env", lambda timeout=None: long_client)
    assert docker_utils.get_long_timeout_docker_client() is long_client  # retried and cached
    assert docker_utils.docker_client_long_timeout is long_client


# --- bulk-summary edge cases -------------------------------------------------


def test_image_sweep_tolerates_null_repo_tags(inject_client):
    """Bulk /images/json may report RepoTags as null; the sweep must skip safely."""
    untagged = FakeImage("sha256:none", None, last_tag_age=timedelta(hours=12))
    leaked = FakeImage("sha256:leak", ["task__x__abc-main:latest"], last_tag_age=timedelta(hours=12))
    client = inject_client(FakeClient(images=[untagged, leaked]))

    assert _sweep_images()["count"] == 1
    assert client.removed_tags == ["task__x__abc-main:latest"]


def test_container_sweep_matches_when_alias_precedes_primary_name(inject_client):
    """Names ordering is not guaranteed; an alias listed first must not hide a candidate."""
    stale = FakeContainer("task__x__abc-main-1", "exited")
    stale.summary_names = ["/other/link-alias", "/task__x__abc-main-1"]
    inject_client(FakeClient(containers=[stale]))

    assert _sweep_containers()["count"] == 1
    assert stale.removed


@pytest.mark.anyio
async def test_validator_startup_honors_dry_run_and_pressure_gates_build_cache(monkeypatch):
    from validator import main as validator_main

    monkeypatch.setattr(validator_main.config, "RIDGES_ENVIRONMENT_TYPE", "docker")
    monkeypatch.setattr(validator_main.config, "CLEANUP_ENABLED", True)
    monkeypatch.setattr(validator_main.config, "CLEANUP_DOCKER_ENABLED", True)
    monkeypatch.setattr(validator_main.config, "CLEANUP_DOCKER_DRY_RUN", True)
    monkeypatch.setattr(validator_main.config, "CLEANUP_STOPPED_GRACE_MINUTES", 45)
    monkeypatch.setattr(validator_main.config, "CLEANUP_RUNNING_TTL_HOURS", 4)
    monkeypatch.setattr(validator_main.config, "CLEANUP_DISK_PRESSURE_PERCENT", 75)
    cleanup_kwargs = {}
    prune_kwargs = {}

    async def metrics():
        return type("Metrics", (), {"disk_percent": 90.0})()

    def cleanup(**kwargs):
        cleanup_kwargs.update(kwargs)
        return {"count": 0, "names": []}

    def prune(**kwargs):
        prune_kwargs.update(kwargs)
        return {"image_bytes": 0, "build_bytes": 0}

    class FakeLoop:
        def add_signal_handler(self, *_args):
            pass

    monkeypatch.setattr(validator_main, "get_system_metrics", metrics)
    monkeypatch.setattr(validator_main, "cleanup_harbor_docker_resources", cleanup)
    monkeypatch.setattr(validator_main, "prune_docker_disk_resources", prune)
    monkeypatch.setattr(validator_main.asyncio, "get_running_loop", lambda: FakeLoop())

    await validator_main._run_startup_tasks()

    assert cleanup_kwargs["dry_run"] is True
    assert prune_kwargs == {"include_build_cache": True, "dry_run": True, "until": "1h"}
