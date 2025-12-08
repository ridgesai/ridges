class TreeNode:
    def __init__(self, data: str, left: "TreeNode | None" = None, right: "TreeNode | None" = None):
        self.data = None
        self.left = None
        self.right = None

    def __str__(self):
        return f'TreeNode(data={self.data}, left={self.left}, right={self.right})'


class BinarySearchTree:
    def __init__(self, tree_data: list[str]):
        pass

    def data(self) -> TreeNode:
        pass

    def sorted_data(self) -> list[str]:
        pass
