class Zipper:
    @staticmethod
    def from_tree(tree) -> 'Zipper':
        pass

    def value(self):
        pass

    def set_value(self, value) -> 'Zipper':
        pass

    def left(self) -> 'Zipper' | None:
        pass

    def set_left(self, left) -> 'Zipper':
        pass

    def right(self) -> 'Zipper' | None:
        pass

    def set_right(self, right) -> 'Zipper':
        pass

    def up(self) -> 'Zipper' | None:
        pass

    def to_tree(self) -> dict:
        pass
