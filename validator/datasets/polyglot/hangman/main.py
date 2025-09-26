# Game status categories
# Change the values as you see fit
STATUS_WIN = 'win'
STATUS_LOSE = 'lose'
STATUS_ONGOING = 'ongoing'


class Hangman:
    def __init__(self, word):
        self.remaining_guesses = 9
        self.status = STATUS_ONGOING

    def guess(self, char) -> None:
        pass

    def get_masked_word(self) -> str:
        pass

    def get_status(self) -> str:
        pass
