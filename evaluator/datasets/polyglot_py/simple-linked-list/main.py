class EmptyListException(Exception):
    pass


class Node:
    def __init__(self, value: int):
        pass

    def value(self) -> int:
        pass

    def next(self) -> "Node | None":
        pass


class LinkedList:
    def __init__(self, values: list | None = None):
        pass

    def __iter__(self):
        pass

    def __len__(self) -> int:
        pass

    def head(self) -> Node:
        pass

    def push(self, value: int) -> None:
        pass

    def pop(self) -> int:
        pass

    def reversed(self) -> "LinkedList":
        pass
