from datetime import UTC, datetime, timedelta
from typing import Callable

NowFn = Callable[[], datetime]


def expire_h(hours: float) -> datetime:
    """Return a datetime `hours` from now.

    Args:
        hours (float): The number of hours from now.

    Returns:
        datetime: The datetime `hours` from now.
    """
    return datetime.now() + timedelta(hours=hours)


def utcnow() -> datetime:
    """Return the current UTC datetime.

    Returns:
        datetime: The current UTC datetime.
    """
    return datetime.now(UTC)
