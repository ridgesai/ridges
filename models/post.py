from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from models.pagination import PageParams


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

    @classmethod
    def from_record(cls, record: PostRecord, *, image_url: Optional[str]) -> "Post":
        """Build the public view of a stored post. The S3 key and content type are never exposed."""
        return cls(
            **record.model_dump(exclude={"image_s3_key", "image_content_type"}),
            image_url=image_url,
        )


class PostSortField(str, Enum):
    created_at = "created_at"
    featured = "featured"


class PostListQuery(PageParams):
    """Query params for GET /posts."""

    featured: Optional[bool] = Field(None, description="Only return posts with this featured flag")
    sort: str = Field(
        f"-{PostSortField.created_at.value}",
        description=(
            f"One of {', '.join(field.value for field in PostSortField)}; prefix with '-' to sort descending. "
            "Ties are broken by newest first, so '-featured' returns featured posts first, each group newest first."
        ),
    )

    @field_validator("sort")
    @classmethod
    def validate_sort(cls, value: str) -> str:
        PostSortField(value.removeprefix("-"))
        return value

    @property
    def sort_field(self) -> PostSortField:
        return PostSortField(self.sort.removeprefix("-"))

    @property
    def sort_descending(self) -> bool:
        return self.sort.startswith("-")
