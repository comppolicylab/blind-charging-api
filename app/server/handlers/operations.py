import logging

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.background import BackgroundTask

from ..authn.base import NotAuthenticated
from ..config import config
from ..db import Revocation
from ..generated.models import (
    APIStatus,
    ClientCredentialsRevokeTokenRequest,
    ClientCredentialsTokenRequest,
    ClientCredentialsTokenResponse,
)
from ..time import NowFn, utcnow

logger = logging.getLogger(__name__)


async def health_check(request: Request) -> APIStatus:
    """Report health of the API."""
    try:
        res = await request.state.store.ping()
        if not res:
            raise HTTPException(status_code=500, detail="Database ping failed")
        return APIStatus(detail="ok")
    except Exception as e:
        logger.exception("Health check failed")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def _vacuum_revokes(now: NowFn = utcnow):
    """Remove expired tokens."""
    try:
        async with config.authentication.store.driver.async_session_with_args(
            pool_pre_ping=True
        )() as session:
            await Revocation.vacuum(session, now=now)
    except Exception:
        logger.exception("Failed to vacuum revocations")
        raise


async def revoke_access_token(
    request: Request, body: ClientCredentialsRevokeTokenRequest
) -> JSONResponse:
    """Revoke an access token."""
    revoke = getattr(request.state.authn, "revoke_token", None)

    if not revoke:
        raise HTTPException(
            status_code=501,
            detail="Token revocation is not supported by the current authn driver",
        )

    tx = request.state.authn_db
    try:
        await revoke(
            tx, body.client_id, body.client_secret, body.token, now=request.state.now
        )
        task = BackgroundTask(_vacuum_revokes, now=request.state.now)
        return JSONResponse(
            status_code=200, content={"detail": "Token revoked"}, background=task
        )
    except NotAuthenticated as e:
        raise HTTPException(status_code=401, detail="Invalid client credentials") from e
    except Exception as e:
        logger.exception("Failed to revoke token")
        raise HTTPException(status_code=500, detail=str(e)) from e


async def get_access_token(
    request: Request, body: ClientCredentialsTokenRequest
) -> ClientCredentialsTokenResponse:
    """Get an access token."""
    issue = getattr(request.state.authn, "issue_token", None)
    if not issue:
        raise HTTPException(
            status_code=501,
            detail="Token issuance is not supported by the current authn driver",
        )

    tx = request.state.authn_db

    try:
        tok = await issue(tx, body.client_id, body.client_secret, now=request.state.now)
    except NotAuthenticated as e:
        raise HTTPException(status_code=401, detail="Invalid client credentials") from e
    except ValueError as e:
        logger.exception("Invalid token request")
        raise HTTPException(status_code=400, detail="Invalid request") from e
    except Exception as e:
        logger.exception("Failed to issue token")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return ClientCredentialsTokenResponse(
        access_token=tok.token, token_type=tok.token_type, expires_in=tok.expires_in
    )
