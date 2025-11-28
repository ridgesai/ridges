from typing import Callable, Any


def append(list1: list, list2: list) -> list:
    pass


def concat(lists: list[list]) -> list:
    pass


def filter(function: Callable, list: list) -> list:
    pass


def length(list: list) -> int:
    pass


def map(function: Callable, list: list) -> list:
    pass


def foldl(function: Callable, list: list, initial: Any) -> Any:  # function(acc, el)
    pass


def foldr(function: Callable, list: list, initial: Any) -> Any:  # function(acc, el)
    pass


def reverse(list: list) -> list:
    pass
