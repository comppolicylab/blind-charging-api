import base64

from kombu.utils.json import register_type as _register_json_type
from pydantic import AnyUrl, BaseModel

from ..generated.models import OutputFormat


def register_type(t: type[BaseModel]) -> None:
    _register_json_type(t, t.__name__, t.model_dump, t.model_validate)


_register_json_type(AnyUrl, "AnyUrl", str, AnyUrl)

_register_json_type(OutputFormat, "OutputFormat", lambda x: x.value, OutputFormat)


def _bytes2json(b: bytes) -> str:
    return base64.b64encode(b).decode("utf-8")


def _json2bytes(s: str) -> bytes:
    return base64.b64decode(s.encode("utf-8"))


_register_json_type(bytes, "bytes", _bytes2json, _json2bytes)
