import base64

import aiohttp
from fastapi import HTTPException, Request

from ..db import File
from ..generated.models import RedactionRequest, RedactionStatus


async def redact_document(*, request: Request, body: RedactionRequest) -> None:
    content = b""
    if body.document.attachmentType == "LINK":
        async with aiohttp.ClientSession() as session:
            async with session.get(body.document.link) as response:
                content = await response.read()
    elif body.document.attachmentType == "TEXT":
        content = body.document.content.encode("utf-8")
    elif body.document.attachmentType == "BASE64":
        content = base64.b64decode(body.document.content)
    else:
        raise HTTPException(status_code=400, detail="Unsupported attachment type")

    # Insert document into database
    file = File(
        external_id=body.document.documentId,
        jurisdiction_id=body.jurisdictionId,
        case_id=body.caseId,
        content=content,
    )
    await file.save(request.state.db)


async def get_redaction_status(
    *, request: Request, jurisdiction_id: str, case_id: str
) -> RedactionStatus:
    return RedactionStatus(
        jurisdictionId=jurisdiction_id,
        caseId=case_id,
        requests=[],
    )
