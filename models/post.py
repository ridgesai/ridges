from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class PostRecord(BaseModel):
    """A `posts` row as stored. Internal: carries the S3 key, never returned by the API."""

    id: UUID
    title: str
    body: str
    image_s3_key: Optional[str] = None
    image_content_type: Optional[str] = None
    unique_thumbnail_id: Optional[str] = None
    featured: bool
    created_at: datetime
    updated_at: Optional[datetime] = None


class Post(BaseModel):
    """A post as exposed by the API. `image_url` is a short-lived presigned URL."""

    id: UUID
    title: str
    body: str
    image_url: Optional[str] = None
    unique_thumbnail_id: Optional[str] = None
    featured: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
