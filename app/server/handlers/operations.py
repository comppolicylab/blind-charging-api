import logging

from fastapi import HTTPException, Request

from ..generated.models import APIStatus

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
