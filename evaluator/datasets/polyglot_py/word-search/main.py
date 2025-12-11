from typing import List, Optional, Tuple

class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = None
        self.y = None

    def __eq__(self, other: object) -> bool:
        return self.x == other.x and self.y == other.y


class WordSearch:
    def __init__(self, puzzle: List[str]) -> None:
        pass

    def search(self, word: str) -> Optional[Tuple[Point, Point]]:
        pass
