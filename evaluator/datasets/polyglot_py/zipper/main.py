class Zipper:
    # Tree is a dict with keys "value" (int), "left" (dict or None), "right" (dict or None)
    @staticmethod
    def from_tree(tree: dict) -> "Zipper":
        pass

    def value(self) -> int:
        pass

    def set_value(self, value: int) -> "Zipper":
        pass

    def left(self) -> "Zipper | None":
        pass

    def set_left(self, tree: dict | None) -> "Zipper":
        pass

    def right(self) -> "Zipper | None":
        pass

    def set_right(self, tree: dict | None) -> "Zipper":
        pass

    def up(self) -> "Zipper | None":
        pass

    def to_tree(self) -> dict:
        pass
