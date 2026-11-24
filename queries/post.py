from typing import Any, NamedTuple, Optional
from uuid import UUID

import asyncpg

from models.post import PostRecord, PostSortField
from queries.errors import PostMissingImageOrThumbnailError
from utils.database import DatabaseConnection, db_operation


@db_operation
async def create_post(
    conn: DatabaseConnection,
    *,
    post_id: UUID,
    title: str,
    body: str,
    image_s3_key: Optional[str],
    image_content_type: Optional[str],
    thumbnail_id: Optional[str],
    featured: bool,
) -> PostRecord:
    row = await conn.fetchrow(
        """
        INSERT INTO posts (id, title, body, image_s3_key, image_content_type, thumbnail_id, featured)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        post_id,
        title,
        body,
        image_s3_key,
        image_content_type,
        thumbnail_id,
        featured,
    )
    return PostRecord(**row)


@db_operation
async def get_post(conn: DatabaseConnection, post_id: UUID) -> Optional[PostRecord]:
    row = await conn.fetchrow("SELECT * FROM posts WHERE id = $1", post_id)
    return PostRecord(**row) if row is not None else None


# Fixed column names only: the sort value is never interpolated into SQL.
_SORT_COLUMNS: dict[PostSortField, str] = {
    PostSortField.created_at: "created_at",
    PostSortField.featured: "featured",
}


@db_operation
async def list_posts(
    conn: DatabaseConnection,
    *,
    featured: Optional[bool] = None,
    sort: PostSortField = PostSortField.created_at,
    descending: bool = True,
    limit: int,
    offset: int,
) -> tuple[list[PostRecord], int]:
    """Return one page of posts and the total count matching the filter.

    Rows are ordered by `sort`, then newest first, then by id so pagination is stable.
    """
    direction = "DESC" if descending else "ASC"
    order_by = f"{_SORT_COLUMNS[sort]} {direction}, created_at DESC, id DESC"
    rows = await conn.fetch(
        f"""
        SELECT *
        FROM posts
        WHERE $1::boolean IS NULL OR featured = $1
        ORDER BY {order_by}
        LIMIT $2 OFFSET $3
        """,
        featured,
        limit,
        offset,
    )
    total = await conn.fetchval(
        "SELECT COUNT(*) FROM posts WHERE $1::boolean IS NULL OR featured = $1",
        featured,
    )
    return [PostRecord(**row) for row in rows], total


# Columns a partial update may set. Only these names are ever interpolated into SQL.
_UPDATABLE_COLUMNS = frozenset({"title", "body", "image_s3_key", "image_content_type", "thumbnail_id", "featured"})


class PostUpdateResult(NamedTuple):
    record: PostRecord
    # The image key the row held right before this update, read under the row lock.
    previous_image_s3_key: Optional[str]


@db_operation
async def update_post(conn: DatabaseConnection, post_id: UUID, **changes: Any) -> Optional[PostUpdateResult]:
    """Set only the given columns, atomically. Returns None when the post does not exist.

    The row is locked first, so concurrent updates run one after the other and each sees the previous
    one's result. Columns not in `changes` are never written, so an update cannot restore a stale value.

    Raises `PostMissingImageOrThumbnailError` if the result would have neither an image nor a thumbnail_id.
    """
    unknown = changes.keys() - _UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"Cannot update post columns: {sorted(unknown)}")

    assignments = [f"{column} = ${position}" for position, column in enumerate(changes, start=2)]
    set_clause = ", ".join([*assignments, "updated_at = NOW()"])

    async with conn.conn.transaction():
        previous = await conn.fetchrow("SELECT image_s3_key FROM posts WHERE id = $1 FOR UPDATE", post_id)
        if previous is None:
            return None
        try:
            row = await conn.fetchrow(
                f"UPDATE posts SET {set_clause} WHERE id = $1 RETURNING *", post_id, *changes.values()
            )
        except asyncpg.CheckViolationError as exc:
            if exc.constraint_name == "ck_posts_image_or_thumbnail":
                raise PostMissingImageOrThumbnailError() from None
            raise

    return PostUpdateResult(PostRecord(**row), previous["image_s3_key"])


@db_operation
async def delete_post(conn: DatabaseConnection, post_id: UUID) -> Optional[PostRecord]:
    """Delete a post and return the deleted row, or None when it does not exist."""
    row = await conn.fetchrow("DELETE FROM posts WHERE id = $1 RETURNING *", post_id)
    return PostRecord(**row) if row is not None else None
