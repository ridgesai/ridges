from typing import List, Callable, TypeVar

T = TypeVar('T')

def keep(sequence: List[T], predicate: Callable[[T], bool]) -> List[T]:
    pass


def discard(sequence: List[T], predicate: Callable[[T], bool]) -> List[T]:
    pass
