import secrets
from datetime import UTC, datetime
from functools import cached_property
from typing import Literal, NamedTuple, Tuple

import jwt
from argon2 import PasswordHasher
from fastapi import Request
from glowplug import SqliteSettings
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils import uuid7

from ..db import Client, RdbmsConfig, Revocation
from ..time import NowFn, utcnow
from .base import BaseAuthnDriver, NotAuthenticated
from .headers import get_bearer_token_from_header

DEFAULT_SCOPE = "default"
"""The default scope for client credentials tokens."""

TOKEN_TYPE = "Bearer"
"""The token type for client credentials tokens."""

AccessTokenWithInfo = NamedTuple(
    "AccessTokenWithInfo", [("token", str), ("expires_in", int), ("token_type", str)]
)
"""A token with additional information."""


class AccessTokenData(BaseModel):
    """The data contained in an access token JWT."""

    jti: str
    sub: str
    scope: str
    iat: int
    exp: int


class NewClientResponse(BaseModel):
    client_id: str
    client_secret: str


class ClientCredentialsAuthnConfig(BaseModel):
    """Configuration for authenticating requests using OAuth2 client credentials.

    Must be configured with a database to store clients.
    """

    method: Literal["client_credentials"] = "client_credentials"
    store: RdbmsConfig = SqliteSettings(engine="sqlite")
    secret: str | list[str]
    algorithms: list[str] = ["HS256"]
    expiry: int = 60 * 60 * 24

    @cached_property
    def driver(self):
        return ClientCredentialsAuthnDriver(
            self.store, self.secret, self.algorithms, self.expiry
        )


def decode_jwt(
    token: str, keys: list[str], algorithms: list[str], now: NowFn = utcnow
) -> AccessTokenData:
    """Decode a JWT token.

    Args:
        token (str): The JWT token.
        keys (list[str]): The keys to try.
        algorithms (list[str]): The algorithms to try.

    Returns:
        AccessTokenData: The token data.

    Raises:
        jwt.InvalidTokenError: If the token is invalid (either expired or malformed).
    """
    for key in keys:
        try:
            decoded = jwt.decode(
                token,
                key,
                algorithms=algorithms,
                options={
                    "verify_exp": False,
                    "verify_nbf": False,
                },
            )

            # Custom timestamp verification according to the nowfn
            ts = now().timestamp()
            nbf = decoded.get("nbf", None)
            if nbf is not None and ts < nbf:
                raise jwt.PyJWTError("Token not valid yet")

            exp = decoded.get("exp", None)
            if exp is not None and ts > exp:
                raise jwt.PyJWTError("Token expired")

            return AccessTokenData.model_validate(decoded)
        except jwt.InvalidTokenError:
            pass
        except ValidationError as e:
            raise jwt.InvalidTokenError("Token data invalid") from e
    raise jwt.InvalidTokenError("Invalid token")


def encode_jwt(data: AccessTokenData, key: str, algorithm: str) -> str:
    """Encode a JWT token.

    Args:
        data (AccessTokenData): The token data.
        key (str): The key to use.
        algorithm (str): The algorithm to use.

    Returns:
        str: The JWT token.
    """
    return jwt.encode(data.model_dump(), key, algorithm=algorithm)


class ClientCredentialsAuthnDriver(BaseAuthnDriver):
    def __init__(
        self,
        store: RdbmsConfig,
        secret: str | list[str],
        algorithms: list[str],
        expiry_seconds: int,
    ):
        self._store = store
        self._keys = list(secret) if isinstance(secret, str) else secret
        self._algorithms = algorithms
        self._expiry_seconds = expiry_seconds

    async def validate_request(
        self, request: Request, scopes: list[str], now: NowFn = utcnow
    ):
        """Verify the access token on a request.

        Args:
            request (Request): The incoming request.
            scopes (list[str]): The required scopes.

        Raises:
            NotAuthenticated: If the request is not authenticated.
        """
        tx = request.state.authn_db
        token = get_bearer_token_from_header(request)
        if not token:
            raise NotAuthenticated("Missing bearer token")

        try:
            decoded = decode_jwt(token, self._keys, self._algorithms, now=now)
        except jwt.InvalidTokenError as e:
            raise NotAuthenticated("Invalid bearer token") from e

        if await Revocation.check(tx, decoded.jti):
            raise NotAuthenticated("Token revoked")

        request.state.authn_data = decoded

    async def register_client(self, tx: AsyncSession, name: str) -> Tuple[Client, str]:
        """Register a client with the authn system.

        Args:
            tx (AsyncSession): The database transaction.
            name (str): A human-readable name for the client.
        """
        client_secret = secrets.token_urlsafe(32)
        ph = PasswordHasher()
        secret_hash = ph.hash(client_secret)
        client = Client(name=name, secret_hash=secret_hash)
        tx.add(client)
        await tx.flush()
        await tx.refresh(client)
        return NewClientResponse(client_id=client.id.hex(), client_secret=client_secret)

    async def issue_token(
        self, tx: AsyncSession, client_id: str, client_secret: str, now: NowFn = utcnow
    ) -> AccessTokenWithInfo:
        """Issue an access token for a client.

        The token request must include the client ID and secret.

        Args:
            tx (AsyncSession): The database transaction.
            client_id (str): The client ID.
            client_secret (str): The client secret.
            now (NowFn, optional): A function that returns the current time.

        Returns:
            AccessTokenWithInfo: The access token and associated metadata.
        """
        client = await self._get_client(tx, client_id, client_secret)

        ts = int(now().timestamp())
        tok = encode_jwt(
            AccessTokenData(
                jti=uuid7().bytes.hex(),
                sub=client.id.hex(),
                scope=DEFAULT_SCOPE,
                iat=ts,
                exp=ts + self._expiry_seconds,
            ),
            self._keys[0],
            self._algorithms[0],
        )
        return AccessTokenWithInfo(
            token=tok, expires_in=self._expiry_seconds, token_type=TOKEN_TYPE
        )

    async def revoke_token(
        self,
        tx: AsyncSession,
        client_id: str,
        client_secret: str,
        token: str,
        expiration_buffer: int = 60,
        now: NowFn = utcnow,
    ):
        """Revoke an access token.

        Args:
            tx (AsyncSession): The database transaction.
            client_id (str): The client ID.
            client_secret (str): The client secret.
            token (str): The token to revoke.
            expiration_buffer (int, optional): The number of seconds to keep the token
            in the database after revocation. Defaults to 60.
        """
        # Check the client and secret first to ensure permission for the revoke.
        await self._get_client(tx, client_id, client_secret)
        token_data = decode_jwt(token, self._keys, self._algorithms, now=now)
        # Determine an expiration time for the token, based on the real expiration
        # time plus a buffer just to be safe.
        expires_at = datetime.fromtimestamp(token_data.exp + expiration_buffer, UTC)
        await Revocation.revoke(tx, token_data.jti, expires_at)

    async def _get_client(
        self, tx: AsyncSession, client_id: str, client_secret: str
    ) -> Client:
        """Get a client by ID, validated with secret.

        Args:
            tx (AsyncSession): The database transaction.
            client_id (str): The client ID.
            client_secret (str): The client secret.

        Returns:
            Client: The client.

        Raises:
            NotAuthenticated: If the client is not found or the secret is invalid
        """
        client = await Client.get_by_id(tx, bytes.fromhex(client_id))
        if not client:
            raise NotAuthenticated("Invalid client")

        ph = PasswordHasher()
        if not ph.verify(client.secret_hash, client_secret):
            raise NotAuthenticated("Invalid client secret")

        return client
