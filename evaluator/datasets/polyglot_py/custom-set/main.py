class CustomSet:
    def __init__(self, elements: list = []):
        pass

    def isempty(self) -> bool:
        pass

    def __contains__(self, element) -> bool:
        pass

    def issubset(self, other: 'CustomSet') -> bool:
        pass

    def isdisjoint(self, other: 'CustomSet') -> bool:
        pass

    def __eq__(self, other: 'CustomSet') -> bool:
        pass

    def add(self, element) -> None:
        pass

    def intersection(self, other: 'CustomSet') -> 'CustomSet':
        pass

    def __sub__(self, other: 'CustomSet') -> 'CustomSet':
        pass

    def __add__(self, other: 'CustomSet') -> 'CustomSet':
        pass
