from typing import Optional
from uuid import UUID

from models.post import PostRecord
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
    unique_thumbnail_id: Optional[str],
    featured: bool,
) -> PostRecord:
    row = await conn.fetchrow(
        """
        INSERT INTO posts (id, title, body, image_s3_key, image_content_type, unique_thumbnail_id, featured)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING *
        """,
        post_id,
        title,
        body,
        image_s3_key,
        image_content_type,
        unique_thumbnail_id,
        featured,
    )
    return PostRecord(**row)


@db_operation
async def get_post(conn: DatabaseConnection, post_id: UUID) -> Optional[PostRecord]:
    row = await conn.fetchrow("SELECT * FROM posts WHERE id = $1", post_id)
    return PostRecord(**row) if row is not None else None


@db_operation
async def list_posts(
    conn: DatabaseConnection,
    *,
    featured: Optional[bool] = None,
    limit: int,
    offset: int,
) -> tuple[list[PostRecord], int]:
    """Return one page of posts, newest first, and the total count matching the filter."""
    rows = await conn.fetch(
        """
        SELECT *
        FROM posts
        WHERE $1::boolean IS NULL OR featured = $1
        ORDER BY created_at DESC, id DESC
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


@db_operation
async def update_post(
    conn: DatabaseConnection,
    post_id: UUID,
    *,
    title: str,
    body: str,
    image_s3_key: Optional[str],
    image_content_type: Optional[str],
    unique_thumbnail_id: Optional[str],
    featured: bool,
) -> Optional[PostRecord]:
    """Overwrite every editable column of a post. Returns None when the post does not exist."""
    row = await conn.fetchrow(
        """
        UPDATE posts
        SET title = $2,
            body = $3,
            image_s3_key = $4,
            image_content_type = $5,
            unique_thumbnail_id = $6,
            featured = $7,
            updated_at = NOW()
        WHERE id = $1
        RETURNING *
        """,
        post_id,
        title,
        body,
        image_s3_key,
        image_content_type,
        unique_thumbnail_id,
        featured,
    )
    return PostRecord(**row) if row is not None else None


@db_operation
async def delete_post(conn: DatabaseConnection, post_id: UUID) -> Optional[PostRecord]:
    """Delete a post and return the deleted row, or None when it does not exist."""
    row = await conn.fetchrow("DELETE FROM posts WHERE id = $1 RETURNING *", post_id)
    return PostRecord(**row) if row is not None else None
