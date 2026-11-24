from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.config as config
import utils.database as _db
from api.endpoints import admin as admin_endpoint
from api.endpoints import posts as posts_endpoint
from models.post import PostRecord, PostSortField
from queries import post as post_queries
from queries.errors import PostMissingImageOrThumbnailError
from utils import s3 as s3_utils

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP_BYTES = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64
AVIF_BYTES = b"\x00\x00\x00\x20ftypavif" + b"\x00" * 64

ADMIN_KEY = "test-posts-admin-key"
AUTH = {"Authorization": f"Bearer {ADMIN_KEY}"}


class FakeBackend:
    """In-memory stand-in for the posts queries and the S3 helpers."""

    def __init__(self) -> None:
        self.posts: dict[UUID, PostRecord] = {}
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.fail_create = False
        self.update_calls: list[dict] = []
        self._clock = datetime(2026, 1, 1, tzinfo=timezone.utc)

    async def create_post(self, **fields) -> PostRecord:
        if self.fail_create:
            raise RuntimeError("insert failed")
        self._clock += timedelta(minutes=1)  # strictly increasing, so ordering assertions are deterministic
        record = PostRecord(id=fields.pop("post_id"), created_at=self._clock, **fields)
        self.posts[record.id] = record
        return record

    async def get_post(self, post_id: UUID) -> PostRecord | None:
        return self.posts.get(post_id)

    async def list_posts(self, *, featured, sort, descending, limit, offset) -> tuple[list[PostRecord], int]:
        matching = [p for p in self.posts.values() if featured is None or p.featured == featured]
        matching.sort(key=lambda p: p.created_at, reverse=True)  # tie-break: newest first
        if sort is PostSortField.featured:
            matching.sort(key=lambda p: p.featured, reverse=descending)  # stable, keeps the tie-break
        elif not descending:
            matching.reverse()
        return matching[offset : offset + limit], len(matching)

    async def update_post(self, post_id: UUID, **changes) -> post_queries.PostUpdateResult | None:
        # Mirrors the real query: a partial update that enforces the image-or-thumbnail constraint.
        self.update_calls.append(changes)
        existing = self.posts.get(post_id)
        if existing is None:
            return None
        record = existing.model_copy(update={**changes, "updated_at": datetime.now(timezone.utc)})
        if record.image_s3_key is None and record.thumbnail_id is None:
            raise PostMissingImageOrThumbnailError()
        self.posts[post_id] = record
        return post_queries.PostUpdateResult(record, existing.image_s3_key)

    async def delete_post(self, post_id: UUID) -> PostRecord | None:
        return self.posts.pop(post_id, None)

    async def upload_bytes_to_s3(self, path: str, data: bytes, *, content_type: str) -> None:
        self.objects[path] = (data, content_type)

    async def delete_file_from_s3(self, path: str) -> None:
        self.objects.pop(path, None)

    async def generate_presigned_urls(self, keys: list[str], *, ttl_seconds: int) -> dict[str, str]:
        return {key: f"https://s3.test/{key}?ttl={ttl_seconds}" for key in keys}


@pytest.fixture
def backend(monkeypatch) -> FakeBackend:
    fake = FakeBackend()
    for name in ("create_post", "get_post", "list_posts", "update_post", "delete_post"):
        monkeypatch.setattr(post_queries, name, getattr(fake, name))
    for name in ("upload_bytes_to_s3", "delete_file_from_s3", "generate_presigned_urls"):
        monkeypatch.setattr(s3_utils, name, getattr(fake, name))
    monkeypatch.setattr(config, "COLDKEY_BAN_ADMIN_API_KEY", ADMIN_KEY)
    return fake


@pytest.fixture
def client(backend) -> TestClient:
    app = FastAPI()
    app.include_router(admin_endpoint.router, prefix="/admin")
    app.include_router(posts_endpoint.router, prefix="/posts")
    return TestClient(app)


def _create(client: TestClient, *, image: bytes | None = PNG_BYTES, **data):
    payload = {"title": "Hello", "body": "World", **data}
    files = {"image": ("pic.bin", image, "application/octet-stream")} if image is not None else None
    return client.post("/admin/posts", data=payload, files=files, headers=AUTH)


# --- auth -----------------------------------------------------------------


def test_admin_routes_require_bearer_key(client, backend) -> None:
    post_id = uuid4()

    assert client.post("/admin/posts", data={"title": "a", "body": "b"}).status_code == 401
    assert client.patch(f"/admin/posts/{post_id}", data={"featured": "true"}).status_code == 401
    assert client.delete(f"/admin/posts/{post_id}").status_code == 401
    wrong = {"Authorization": "Bearer nope"}
    assert client.post("/admin/posts", data={"title": "a", "body": "b"}, headers=wrong).status_code == 401
    assert backend.posts == {}


def test_list_is_public(client) -> None:
    response = client.get("/posts")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "page": 1, "page_size": 50}


# --- create ---------------------------------------------------------------


def test_create_with_image_stores_original_bytes_in_s3(client, backend) -> None:
    response = _create(client, featured="true")

    assert response.status_code == 201
    post = response.json()
    assert post["title"] == "Hello"
    assert post["body"] == "World"
    assert post["featured"] is True
    assert post["thumbnail_id"] is None
    assert "image_s3_key" not in post

    ((key, (data, content_type)),) = backend.objects.items()
    assert key.startswith(f"posts/{post['id']}/") and key.endswith(".png")
    assert data == PNG_BYTES
    assert content_type == "image/png"
    assert post["image_url"] == f"https://s3.test/{key}?ttl={posts_endpoint.POST_IMAGE_URL_TTL_SECONDS}"


def test_create_with_thumbnail_only(client, backend) -> None:
    response = _create(client, image=None, thumbnail_id="  hero-card  ")

    assert response.status_code == 201
    post = response.json()
    assert post["thumbnail_id"] == "hero-card"
    assert post["image_url"] is None
    assert post["featured"] is False
    assert backend.objects == {}


def test_thumbnail_id_can_repeat_across_posts(client, backend) -> None:
    first = _create(client, image=None, thumbnail_id="hero-card")
    second = _create(client, image=None, thumbnail_id="hero-card")

    assert first.status_code == second.status_code == 201
    assert len(backend.posts) == 2


@pytest.mark.parametrize("thumbnail_id", [None, "   "])
def test_create_requires_image_or_thumbnail(client, backend, thumbnail_id) -> None:
    data = {} if thumbnail_id is None else {"thumbnail_id": thumbnail_id}

    response = _create(client, image=None, **data)

    assert response.status_code == 422
    assert backend.posts == {}


@pytest.mark.parametrize("field", ["title", "body"])
def test_create_rejects_blank_text(client, backend, field) -> None:
    response = _create(client, **{field: "   "})

    assert response.status_code == 422
    assert backend.posts == {} and backend.objects == {}


@pytest.mark.parametrize(
    ("image", "content_type", "extension"),
    [
        (JPEG_BYTES, "image/jpeg", ".jpg"),
        (PNG_BYTES, "image/png", ".png"),
        (WEBP_BYTES, "image/webp", ".webp"),
        (AVIF_BYTES, "image/avif", ".avif"),
    ],
)
def test_create_accepts_supported_formats(client, backend, image, content_type, extension) -> None:
    assert _create(client, image=image).status_code == 201

    ((key, (_, stored_type)),) = backend.objects.items()
    assert key.endswith(extension)
    assert stored_type == content_type


def test_create_rejects_non_image_even_with_image_content_type(client, backend) -> None:
    response = client.post(
        "/admin/posts",
        data={"title": "Hello", "body": "World"},
        files={"image": ("pic.png", b"<svg onload=alert(1)>", "image/png")},
        headers=AUTH,
    )

    assert response.status_code == 415
    assert backend.posts == {} and backend.objects == {}


def test_create_rejects_oversized_image(client, backend, monkeypatch) -> None:
    monkeypatch.setattr(admin_endpoint, "MAX_POST_IMAGE_SIZE_BYTES", 32)

    response = _create(client)

    assert response.status_code == 413
    assert backend.posts == {} and backend.objects == {}


def test_create_removes_uploaded_image_when_insert_fails(client, backend) -> None:
    backend.fail_create = True

    with pytest.raises(RuntimeError, match="insert failed"):
        _create(client)

    assert backend.objects == {}


# --- list -----------------------------------------------------------------


def test_list_filters_by_featured_and_paginates(client) -> None:
    plain = _create(client, title="plain").json()
    featured = _create(client, title="featured", featured="true").json()

    everything = client.get("/posts").json()
    assert everything["total"] == 2
    assert {p["id"] for p in everything["items"]} == {plain["id"], featured["id"]}
    assert all(p["image_url"].startswith("https://s3.test/posts/") for p in everything["items"])

    only_featured = client.get("/posts", params={"featured": "true"}).json()
    assert [p["id"] for p in only_featured["items"]] == [featured["id"]]
    assert only_featured["total"] == 1

    not_featured = client.get("/posts", params={"featured": "false"}).json()
    assert [p["id"] for p in not_featured["items"]] == [plain["id"]]

    page_two = client.get("/posts", params={"page": 2, "page_size": 1}).json()
    assert len(page_two["items"]) == 1
    assert page_two["total"] == 2


def test_list_sort_featured_first(client) -> None:
    featured_old = _create(client, title="featured old", featured="true").json()["id"]
    plain = _create(client, title="plain").json()["id"]
    featured_new = _create(client, title="featured new", featured="true").json()["id"]

    def ids(**params) -> list[str]:
        response = client.get("/posts", params=params)
        assert response.status_code == 200
        return [p["id"] for p in response.json()["items"]]

    assert ids() == [featured_new, plain, featured_old]
    assert ids(sort="-created_at") == [featured_new, plain, featured_old]
    assert ids(sort="created_at") == [featured_old, plain, featured_new]
    assert ids(sort="-featured") == [featured_new, featured_old, plain]
    assert ids(sort="-featured", page=2, page_size=2) == [plain]


@pytest.mark.parametrize("sort", ["title", "--featured", ""])
def test_list_rejects_unknown_sort(client, sort) -> None:
    assert client.get("/posts", params={"sort": sort}).status_code == 422


# --- update ---------------------------------------------------------------


def test_patch_makes_existing_post_featured_without_touching_other_fields(client, backend) -> None:
    created = _create(client, thumbnail_id="hero-card").json()
    objects_before = dict(backend.objects)

    response = client.patch(f"/admin/posts/{created['id']}", data={"featured": "true"}, headers=AUTH)

    assert response.status_code == 200
    updated = response.json()
    assert updated["featured"] is True
    assert updated["updated_at"] is not None
    for field in ("title", "body", "thumbnail_id", "image_url"):
        assert updated[field] == created[field]
    assert backend.objects == objects_before


def test_patch_updates_text(client) -> None:
    created = _create(client).json()

    response = client.patch(
        f"/admin/posts/{created['id']}", data={"title": " New title ", "body": "New body"}, headers=AUTH
    )

    assert response.status_code == 200
    assert response.json()["title"] == "New title"
    assert response.json()["body"] == "New body"


def test_patch_replaces_image_and_deletes_old_object(client, backend) -> None:
    created = _create(client).json()
    (old_key,) = backend.objects

    response = client.patch(
        f"/admin/posts/{created['id']}",
        files={"image": ("new.jpg", JPEG_BYTES, "image/jpeg")},
        headers=AUTH,
    )

    assert response.status_code == 200
    ((new_key, (data, content_type)),) = backend.objects.items()
    assert new_key != old_key and new_key.endswith(".jpg")
    assert data == JPEG_BYTES
    assert content_type == "image/jpeg"
    assert new_key in response.json()["image_url"]


def test_patch_empty_image_removes_it_and_keeps_post_with_thumbnail(client, backend) -> None:
    created = _create(client, thumbnail_id="hero-card").json()

    response = client.patch(f"/admin/posts/{created['id']}", data={"image": ""}, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["image_url"] is None
    assert backend.objects == {}


def test_patch_rejects_leaving_post_without_image_or_thumbnail(client, backend) -> None:
    image_only = _create(client).json()
    thumbnail_only = _create(client, image=None, thumbnail_id="hero-card").json()
    objects_before = dict(backend.objects)

    removed_image = client.patch(f"/admin/posts/{image_only['id']}", data={"image": ""}, headers=AUTH)
    cleared_thumbnail = client.patch(f"/admin/posts/{thumbnail_only['id']}", data={"thumbnail_id": ""}, headers=AUTH)

    assert removed_image.status_code == 422
    assert cleared_thumbnail.status_code == 422
    assert backend.objects == objects_before
    assert backend.posts[UUID(thumbnail_only["id"])].thumbnail_id == "hero-card"


def test_patch_can_swap_thumbnail_for_image(client, backend) -> None:
    created = _create(client, image=None, thumbnail_id="hero-card").json()

    response = client.patch(
        f"/admin/posts/{created['id']}",
        data={"thumbnail_id": ""},
        files={"image": ("new.png", PNG_BYTES, "image/png")},
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json()["thumbnail_id"] is None
    assert response.json()["image_url"] is not None


def test_patch_omitted_image_and_thumbnail_are_left_unchanged(client, backend) -> None:
    created = _create(client, thumbnail_id="hero-card").json()
    objects_before = dict(backend.objects)

    response = client.patch(
        f"/admin/posts/{created['id']}",
        files={"title": (None, "Renamed")},  # multipart body without image / thumbnail_id parts
        headers=AUTH,
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Renamed"
    assert response.json()["thumbnail_id"] == "hero-card"
    assert response.json()["image_url"] == created["image_url"]
    assert backend.objects == objects_before


def test_patch_writes_only_the_fields_that_were_sent(client, backend) -> None:
    created = _create(client, thumbnail_id="hero-card").json()

    response = client.patch(f"/admin/posts/{created['id']}", data={"body": "Edited"}, headers=AUTH)

    assert response.status_code == 200
    # Untouched columns are never written, so a concurrent update to them cannot be overwritten.
    assert backend.update_calls == [{"body": "Edited"}]


@pytest.mark.parametrize("encoding", ["urlencoded", "multipart"])
def test_patch_empty_thumbnail_id_alone_clears_it_and_keeps_image(client, backend, encoding) -> None:
    created = _create(client, thumbnail_id="hero-card").json()
    objects_before = dict(backend.objects)
    form = {"data": {"thumbnail_id": ""}} if encoding == "urlencoded" else {"files": {"thumbnail_id": (None, "")}}

    response = client.patch(f"/admin/posts/{created['id']}", headers=AUTH, **form)

    # FastAPI maps the empty value to the default before min_length runs, so this is not a validation error.
    assert response.status_code == 200
    assert response.json()["thumbnail_id"] is None
    assert response.json()["image_url"] == created["image_url"]
    assert backend.objects == objects_before


def test_patch_rejected_by_constraint_removes_uploaded_image(client, backend, monkeypatch) -> None:
    created = _create(client).json()
    objects_before = dict(backend.objects)

    async def update_violating_constraint(post_id, **changes):
        raise PostMissingImageOrThumbnailError()

    monkeypatch.setattr(post_queries, "update_post", update_violating_constraint)
    response = client.patch(
        f"/admin/posts/{created['id']}", files={"image": ("new.png", PNG_BYTES, "image/png")}, headers=AUTH
    )

    assert response.status_code == 422
    assert backend.objects == objects_before


def test_patch_clears_image_and_thumbnail_with_empty_multipart_fields(client, backend) -> None:
    with_both = _create(client, thumbnail_id="hero-card").json()

    cleared_image = client.patch(f"/admin/posts/{with_both['id']}", files={"image": (None, "")}, headers=AUTH)
    assert cleared_image.status_code == 200
    assert cleared_image.json()["image_url"] is None
    assert cleared_image.json()["thumbnail_id"] == "hero-card"
    assert backend.objects == {}

    restored = client.patch(
        f"/admin/posts/{with_both['id']}",
        files={"image": ("new.png", PNG_BYTES, "image/png"), "thumbnail_id": (None, "")},
        headers=AUTH,
    )
    assert restored.status_code == 200
    assert restored.json()["thumbnail_id"] is None
    assert restored.json()["image_url"] is not None


def test_patch_unknown_post_is_404(client) -> None:
    response = client.patch(f"/admin/posts/{uuid4()}", data={"featured": "true"}, headers=AUTH)

    assert response.status_code == 404


# --- delete ---------------------------------------------------------------


def test_delete_removes_post_and_image(client, backend) -> None:
    created = _create(client).json()

    response = client.delete(f"/admin/posts/{created['id']}", headers=AUTH)

    assert response.status_code == 204
    assert backend.posts == {}
    assert backend.objects == {}
    assert client.delete(f"/admin/posts/{created['id']}", headers=AUTH).status_code == 404


# --- queries against a real database (also exercises the migration) --------


@pytest.fixture
async def clean_posts(postgres_db):
    yield
    async with _db.pool.acquire() as conn:
        await conn.execute("TRUNCATE posts")


async def _insert(**overrides) -> PostRecord:
    fields = {
        "post_id": uuid4(),
        "title": "Hello",
        "body": "World",
        "image_s3_key": None,
        "image_content_type": None,
        "thumbnail_id": "hero-card",
        "featured": False,
        **overrides,
    }
    return await post_queries.create_post(**fields)


@pytest.mark.anyio
async def test_queries_round_trip(clean_posts) -> None:
    older = await _insert(title="older")
    newer = await _insert(
        title="newer",
        featured=True,
        image_s3_key="posts/x/y.png",
        image_content_type="image/png",
        thumbnail_id="hero-card",  # same id as `older`: repeats are allowed
    )

    records, total = await post_queries.list_posts(featured=None, limit=10, offset=0)
    assert [r.id for r in records] == [newer.id, older.id]
    assert total == 2

    records, total = await post_queries.list_posts(featured=True, limit=10, offset=0)
    assert [r.id for r in records] == [newer.id]
    assert total == 1

    records, total = await post_queries.list_posts(featured=None, limit=1, offset=1)
    assert [r.id for r in records] == [older.id]
    assert total == 2

    assert older.updated_at is None
    updated, previous_image_key = await post_queries.update_post(older.id, title="older, edited", featured=True)
    assert previous_image_key is None
    assert updated.featured is True
    assert updated.title == "older, edited"
    assert updated.body == older.body and updated.thumbnail_id == older.thumbnail_id  # untouched columns kept
    assert updated.updated_at is not None
    assert await post_queries.get_post(older.id) == updated

    assert (await post_queries.delete_post(newer.id)).image_s3_key == "posts/x/y.png"
    assert await post_queries.get_post(newer.id) is None
    assert await post_queries.delete_post(newer.id) is None
    assert await post_queries.update_post(newer.id, title="gone") is None


@pytest.mark.anyio
async def test_update_query_enforces_image_or_thumbnail(clean_posts) -> None:
    thumbnail_only = await _insert()

    with pytest.raises(PostMissingImageOrThumbnailError):
        await post_queries.update_post(thumbnail_only.id, thumbnail_id=None)
    assert await post_queries.get_post(thumbnail_only.id) == thumbnail_only

    with pytest.raises(ValueError, match="id"):
        await post_queries.update_post(thumbnail_only.id, id=uuid4())


@pytest.mark.anyio
async def test_concurrent_updates_serialize_and_never_restore_a_replaced_image(clean_posts) -> None:
    post = await _insert(image_s3_key="posts/p/k1.png", image_content_type="image/png")

    # Hold the row lock so both updates queue behind it, then release it and let them run.
    async with _db.pool.acquire() as holder:
        transaction = holder.transaction()
        await transaction.start()
        await holder.execute("SELECT 1 FROM posts WHERE id = $1 FOR UPDATE", post.id)
        replace_image = asyncio.create_task(
            post_queries.update_post(post.id, image_s3_key="posts/p/k2.png", image_content_type="image/png")
        )
        edit_body = asyncio.create_task(post_queries.update_post(post.id, body="Edited"))
        await asyncio.sleep(0.2)
        assert not replace_image.done() and not edit_body.done()
        await transaction.commit()
    image_result, body_result = await asyncio.gather(replace_image, edit_body)

    final = await post_queries.get_post(post.id)
    assert final.image_s3_key == "posts/p/k2.png"  # the body-only update did not restore k1
    assert final.body == "Edited"  # and the image update did not undo the body edit
    assert image_result.previous_image_s3_key == "posts/p/k1.png"
    assert body_result.previous_image_s3_key in {"posts/p/k1.png", "posts/p/k2.png"}


@pytest.mark.anyio
async def test_list_query_sorts_featured_first(clean_posts) -> None:
    featured_old = await _insert(title="featured old", featured=True)
    plain = await _insert(title="plain")
    featured_new = await _insert(title="featured new", featured=True)

    async def ids(**kwargs) -> list[UUID]:
        records, _ = await post_queries.list_posts(featured=None, limit=10, offset=0, **kwargs)
        return [r.id for r in records]

    assert await ids() == [featured_new.id, plain.id, featured_old.id]
    assert await ids(sort=PostSortField.created_at, descending=False) == [featured_old.id, plain.id, featured_new.id]
    assert await ids(sort=PostSortField.featured) == [featured_new.id, featured_old.id, plain.id]
    assert await ids(sort=PostSortField.featured, descending=False) == [plain.id, featured_new.id, featured_old.id]


@pytest.mark.anyio
async def test_database_rejects_post_without_image_or_thumbnail(clean_posts) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(thumbnail_id=None)
