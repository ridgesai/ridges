from enum import Enum


class QueueStage(str, Enum):
    pre_screening = "pre_screening"
    screener_1 = "screener_1"
    screener_2 = "screener_2"
    validator = "validator"
