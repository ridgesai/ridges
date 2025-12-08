from typing import Any


class Node:
    def __init__(self, value: Any, succeeding: 'Node | None' = None, previous: 'Node | None' = None) -> None:
        pass


class LinkedList:
    def __init__(self) -> None:
        pass

    def __len__(self) -> int:
        pass

    def push(self, value: Any) -> None:
        pass

    def pop(self) -> Any:
        pass

    def shift(self) -> Any:
        pass

    def unshift(self, value: Any) -> None:
        pass

    def delete(self, value: Any) -> None:
        pass
