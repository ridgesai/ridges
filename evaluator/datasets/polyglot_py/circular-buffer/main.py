class BufferFullException(BufferError):
    """Exception raised when CircularBuffer is full.

    message: explanation of the error.

    """
    def __init__(self, message: str):
        pass


class BufferEmptyException(BufferError):
    """Exception raised when CircularBuffer is empty.

    message: explanation of the error.

    """
    def __init__(self, message: str):
        pass


class CircularBuffer:
    def __init__(self, capacity: int):
        pass

    def read(self) -> str:
        pass

    def write(self, data: str) -> None:
        pass

    def overwrite(self, data: str) -> None:
        pass

    def clear(self) -> None:
        pass
