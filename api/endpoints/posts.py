"""CMS posts: the public list endpoint. The admin create/update/delete endpoints live in `admin.py`.

Post images are stored unmodified in S3 under `posts/`; the database only holds the object key and
responses carry short-lived presigned URLs.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Query

from models.pagination import Page, PageParams
from models.post import Post, PostRecord
from queries import post as post_queries
from utils import s3

router = APIRouter(tags=["posts"])

POST_IMAGE_URL_TTL_SECONDS = 60 * 60


class PostListQuery(PageParams):
    featured: Optional[bool] = None


# /posts
@router.get("", response_model=Page[Post])
async def get_posts(query: Annotated[PostListQuery, Query()]) -> Page[Post]:
    """List posts, newest first. `featured` optionally filters by the featured flag.

    `image_url` is a presigned URL valid for one hour; do not persist it.
    """
    records, total = await post_queries.list_posts(
        featured=query.featured,
        limit=query.page_size,
        offset=(query.page - 1) * query.page_size,
    )
    return Page[Post](
        items=await to_public_posts(records),
        total=total,
        page=query.page,
        page_size=query.page_size,
    )


async def to_public_posts(records: list[PostRecord]) -> list[Post]:
    """Presign all image URLs with a single S3 client, then build the public views."""
    keys = [record.image_s3_key for record in records if record.image_s3_key]
    urls = await s3.generate_presigned_urls(keys, ttl_seconds=POST_IMAGE_URL_TTL_SECONDS)
    return [Post.from_record(record, image_url=urls.get(record.image_s3_key)) for record in records]
