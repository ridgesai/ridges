from typing import Callable, List, TypeVar

T = TypeVar('T')
U = TypeVar('U')

def accumulate(collection: List[T], operation: Callable[[T], U]) -> List[U]:
    pass
