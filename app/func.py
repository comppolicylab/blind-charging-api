from enum import Enum
from typing import Any, Callable


class ExceptionPolicy(Enum):
    """How to deal with exceptions in higher-order functions.

    EAGER: Raise exceptions as soon as they occur.
    LAZY: Allow exceptions to accumulate and raise them all at the end.
    QUIET: Suppress exceptions.
    """

    EAGER = "eager"
    LAZY = "lazy"
    QUIET = "quiet"


def allf(
    *funcs: Callable, exc_policy: ExceptionPolicy = ExceptionPolicy.LAZY
) -> Callable:
    """Combine multiple functions into a single function.

    Exceptions are handled according to the `exc_policy`.

    The return value is a list of the results of each function, in order.

    Args:
        *funcs: The functions to combine.
        exc_policy: How to handle exceptions.

    Returns:
        Callable: A function that runs all the input functions.
    """
    exceptions: list[Exception] = []

    def _allf(*args, **kwargs) -> list[Any]:
        results: list[Any] = []

        for func in funcs:
            try:
                result = func(*args, **kwargs)
                results.append(result)
            except Exception as e:
                exceptions.append(e)
                results.append(None)
                if exc_policy == ExceptionPolicy.EAGER:
                    raise e
        if exc_policy == ExceptionPolicy.LAZY:
            for exception in exceptions:
                raise exception

        return results

    return _allf
