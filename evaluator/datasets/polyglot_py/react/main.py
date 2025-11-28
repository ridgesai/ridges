from typing import Callable


class InputCell:
    def __init__(self, initial_value: int):
        self.value = None


class ComputeCell:
    def __init__(self, inputs: list, compute_function: Callable):
        self.value = None

    def add_callback(self, callback: Callable) -> None:
        pass

    def remove_callback(self, callback: Callable) -> None:
        pass
    