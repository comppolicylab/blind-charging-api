from fastapi import HTTPException, Request

from ..generated.models import APIStatus


async def health_check(request: Request) -> APIStatus:
    """Report health of the API."""
    try:
        res = await request.state.tx.ping()
        if not res:
            raise HTTPException(status_code=500, detail="Database ping failed")
        return APIStatus(detail="ok")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
