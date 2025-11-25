class Record:
    def __init__(self, record_id: int, parent_id: int):
        self.record_id = record_id
        self.parent_id = parent_id


class Node:
    def __init__(self, node_id: int):
        self.node_id = node_id
        self.children = []


def BuildTree(records: list[Record]) -> Node | None:
    # Error messages required by tests:
    # - "Record id is invalid or out of order." - when IDs are non-continuous or root is missing
    # - "Node parent_id should be smaller than it's record_id." - when parent_id >= record_id (except root)
    # - "Only root should have equal record and parent id." - when non-root has record_id == parent_id
    root = None
    records.sort(key=lambda x: x.record_id)
    ordered_id = [i.record_id for i in records]
    if records:
        if ordered_id[-1] != len(ordered_id) - 1:
            raise ValueError('Record id is invalid or out of order.')
        if ordered_id[0] != 0:
            raise ValueError('Record id is invalid or out of order.')
    trees = []
    parent = {}
    for i in range(len(ordered_id)):
        for j in records:
            if ordered_id[i] == j.record_id:
                if j.record_id == 0:
                    if j.parent_id != 0:
                        raise ValueError("Node parent_id should be smaller than it's record_id.")
                if j.record_id < j.parent_id:
                    raise ValueError("Node parent_id should be smaller than it's record_id.")
                if j.record_id == j.parent_id:
                    if j.record_id != 0:
                        raise ValueError('Only root should have equal record and parent id.')
                trees.append(Node(ordered_id[i]))
    for i in range(len(ordered_id)):
        for j in trees:
            if i == j.node_id:
                parent = j
        for j in records:
            if j.parent_id == i:
                for k in trees:
                    if k.node_id == 0:
                        continue
                    if j.record_id == k.node_id:
                        child = k
                        parent.children.append(child)
    if len(trees) > 0:
        root = trees[0]
    return root
