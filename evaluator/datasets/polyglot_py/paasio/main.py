import io
from typing import Any, Optional


class MeteredFile(io.BufferedRandom):
    """Implement using a subclassing model."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "MeteredFile":
        pass

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Optional[bool]:
        pass

    def __iter__(self) -> "MeteredFile":
        pass

    def __next__(self) -> bytes:
        pass

    def read(self, size: int = -1) -> bytes:
        pass

    @property
    def read_bytes(self) -> int:
        pass

    @property
    def read_ops(self) -> int:
        pass

    def write(self, b: bytes) -> int:
        pass

    @property
    def write_bytes(self) -> int:
        pass

    @property
    def write_ops(self) -> int:
        pass


class MeteredSocket:
    """Implement using a delegation model."""

    def __init__(self, socket: Any) -> None:
        pass

    def __enter__(self) -> "MeteredSocket":
        pass

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Optional[bool]:
        pass

    def recv(self, bufsize: int, flags: int = 0) -> bytes:
        pass

    @property
    def recv_bytes(self) -> int:
        pass

    @property
    def recv_ops(self) -> int:
        pass

    def send(self, data: bytes, flags: int = 0) -> int:
        pass

    @property
    def send_bytes(self) -> int:
        pass

    @property
    def send_ops(self) -> int:
        pass
