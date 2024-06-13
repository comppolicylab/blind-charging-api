from datetime import datetime, timedelta


def expire_h(hours: float) -> datetime:
    """Return a datetime `hours` from now.

    Args:
        hours (float): The number of hours from now.

    Returns:
        datetime: The datetime `hours` from now.
    """
    return datetime.now() + timedelta(hours=hours)
