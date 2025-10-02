class EmptyListException(Exception):
    pass


class Node:
    def __init__(self, value):
        pass

    def value(self):
        pass

    def next(self) -> 'Node' | None:
        pass


class LinkedList:
    def __init__(self, values=None):
        pass

    def __iter__(self):
        pass

    def __len__(self):
        pass

    def head(self) -> 'Node':
        pass

    def push(self, value) -> None:
        pass

    def pop(self):
        pass

    def reversed(self) -> 'LinkedList':
        pass
