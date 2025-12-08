from datetime import date


# subclassing the built-in ValueError to create MeetupDayException
class MeetupDayException(ValueError):
    """Exception raised when the Meetup weekday and count do not result in a valid date.

    message: explanation of the error.

    """
    def __init__(self) -> None:
        pass


def meetup(year: int, month: int, week: str, day_of_week: str) -> date:
    pass
