from functools import cached_property
from typing import Literal

from fastapi import Request
from pydantic import BaseModel

from .base import BaseAuthnDriver, NotAuthenticated
from .headers import get_bearer_token_from_header


class PresharedSecretAuthnConfig(BaseModel):
    """Configuration for authenticating requests using a preshared secret.

    Secret can be a single string or a list of strings.

    Multiple strings can be used to support multiple clients, or to rotate
    the secret without gaps.
    """

    method: Literal["preshared"] = "preshared"
    secret: str | list[str]

    @cached_property
    def driver(self):
        return PresharedSecretAuthnDriver(self.secret)


class PresharedSecretAuthnDriver(BaseAuthnDriver):
    """Validate requests from a client using a preshared secret.

    Token is expected to be passed in the Authorization header as a bearer token.

    Example:
        # Preshared secret is 'foo'
        # Headers from client must include:
        {
            'Authorization': 'Bearer foo'
        }
    """

    def __init__(self, secrets: str | list[str]):
        self._secrets = set(secrets) if isinstance(secrets, list) else {secrets}

    async def validate_request(self, request: Request, scopes: list[str]):
        token = get_bearer_token_from_header(request)
        if not token:
            raise NotAuthenticated("Missing bearer token")
        if token not in self._secrets:
            raise NotAuthenticated("Invalid bearer token")
