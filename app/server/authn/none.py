from functools import cached_property
from typing import Literal

from fastapi import Request
from pydantic import BaseModel

from ..time import NowFn, utcnow
from .base import BaseAuthnDriver


class NoAuthnConfig(BaseModel):
    method: Literal["none"] = "none"

    @cached_property
    def driver(self):
        return NoAuthnDriver()


class NoAuthnDriver(BaseAuthnDriver):
    async def validate_request(
        self, request: Request, scopes: list[str], now: NowFn = utcnow
    ):
        pass
