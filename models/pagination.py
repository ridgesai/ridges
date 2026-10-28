from typing import Callable, Generic, Sequence, TypeVar

from pydantic import BaseModel, Field

# Raw item type before being transformed into the API facing type
S = TypeVar("S")
# Transformed type exposed at the API level
T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class PageParams(BaseModel):
    """Page-based pagination query params. Extend it to add endpoint-specific params."""

    page: int = Field(1, ge=1)
    page_size: int = Field(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


def paginate(items: Sequence[S], params: PageParams, transform: Callable[[S], T]) -> Page[T]:
    """Slice one page out of `items`, applying `transform` only to the items on that page."""
    start = (params.page - 1) * params.page_size
    return Page[T](
        items=[transform(item) for item in items[start : start + params.page_size]],
        total=len(items),
        page=params.page,
        page_size=params.page_size,
    )
