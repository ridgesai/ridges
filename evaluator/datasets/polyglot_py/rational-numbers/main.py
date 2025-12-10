class Rational:
    def __init__(self, numer: int, denom: int) -> None:
        self.numer = None
        self.denom = None

    def __eq__(self, other: object) -> bool:
        return self.numer == other.numer and self.denom == other.denom

    def __repr__(self) -> str:
        return f'{self.numer}/{self.denom}'

    def __add__(self, other: 'Rational') -> 'Rational':
        pass

    def __sub__(self, other: 'Rational') -> 'Rational':
        pass

    def __mul__(self, other: 'Rational') -> 'Rational':
        pass

    def __truediv__(self, other: 'Rational') -> 'Rational':
        pass

    def __abs__(self) -> 'Rational':
        pass

    def __pow__(self, power: int) -> 'Rational':
        pass

    def __rpow__(self, base: float) -> float:
        pass
