"""Helpers for validating uploaded images. Framework-agnostic: failures raise `InvalidImageError` subclasses."""

from typing import Optional, Protocol

# Formats we accept, mapped to the file extension used when storing them.
IMAGE_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/avif": "avif",
}

_READ_CHUNK_SIZE = 1024 * 1024


class AsyncReadable(Protocol):
    """Anything with an async `read(size)`, e.g. FastAPI/Starlette's `UploadFile`."""

    async def read(self, size: int = -1) -> bytes: ...


class InvalidImageError(ValueError):
    """Base class for rejected images."""


class EmptyImageError(InvalidImageError):
    pass


class ImageTooLargeError(InvalidImageError):
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        super().__init__(f"Image must not exceed {max_bytes // (1024 * 1024)}MB")


class UnsupportedImageError(InvalidImageError):
    def __init__(self):
        super().__init__("Image must be a JPEG, PNG, WebP or AVIF file")


def sniff_image_content_type(data: bytes) -> Optional[str]:
    """Identify the image format from its magic bytes; a client-declared content type is not trusted."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis"):
        return "image/avif"
    return None


async def read_image(source: AsyncReadable, *, max_bytes: int) -> tuple[bytes, str]:
    """Read an image in chunks, stopping as soon as it exceeds `max_bytes`.

    Returns the unmodified bytes and the sniffed content type.
    """
    size = 0
    chunks: list[bytes] = []
    while chunk := await source.read(_READ_CHUNK_SIZE):
        size += len(chunk)
        if size > max_bytes:
            raise ImageTooLargeError(max_bytes)
        chunks.append(chunk)
    data = b"".join(chunks)

    if not data:
        raise EmptyImageError("Image is empty")
    content_type = sniff_image_content_type(data)
    if content_type is None:
        raise UnsupportedImageError()
    return data, content_type
