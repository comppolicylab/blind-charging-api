from fastapi import Request


def get_bearer_token_from_header(
    request: Request, header: str = "Authorization"
) -> str | None:
    """Get the bearer token from the Authorization header.

    Args:
        request (Request): The incoming request.
        header (str, optional): The header to get the token from.

    Returns:
        str | None: The bearer token, or None if not found.
    """
    auth = request.headers.get(header)
    if not auth:
        return None

    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer":
        return None

    return token
