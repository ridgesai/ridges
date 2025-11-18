# Game status categories
# Change the values as you see fit
STATUS_WIN = 'win'
STATUS_LOSE = 'lose'
STATUS_ONGOING = 'ongoing'


class Hangman:
    def __init__(self, word: str):
        self.remaining_guesses = 10
        self.status = STATUS_ONGOING

    def guess(self, char: str) -> None:
        pass

    def get_masked_word(self) -> str:
        pass

    def get_status(self) -> str:
        pass
