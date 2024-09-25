import json
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Any, Callable, Generic, Type, TypeVar, cast

import hcl2
import typer

logger = logging.getLogger(__name__)


T = TypeVar("T")

U = TypeVar("U")


@dataclass
class DataType(Generic[T, U]):
    tf_type: str
    py_type: Type[U]
    cli_type: Type[T]
    parser: Callable[[T], U] = lambda x: cast(U, x)


@dataclass
class Variable(Generic[T, U]):
    name: str
    description: str
    type_: DataType[T, U]
    required: bool
    value: U | None = None


def _ensure_bool(v: bool | str) -> bool:
    if isinstance(v, bool):
        return v
    return v.lower() in {"yes", "true", "t", "1"}


_DATA_TYPES: list[DataType[Any, Any]] = [
    DataType("${string}", str, str),
    DataType("${bool}", bool, bool, _ensure_bool),
    DataType("${map(string)}", dict[str, str], str, json.loads),
]


_TYPE_MAP: dict[str, DataType] = {dt.tf_type: dt for dt in _DATA_TYPES}


_CONDITIONAL: dict[str, Callable[[dict[str, Variable]], bool]] = {
    "app_auth_secret": lambda vs: cast(str, vs["app_auth"].value) != "none",
    "waf": lambda vs: bool(vs["expose_app"].value),
}


_AUTO_GENERATE: dict[str, Callable[[], Any]] = {
    "db_password": lambda: secrets.token_urlsafe(32),
    "app_auth_secret": lambda: secrets.token_urlsafe(64),
}


def _parse_var(raw_var: dict) -> Variable:
    """Parse a variable dictionary object from the terraform file."""
    keys = list(raw_var.keys())
    if len(keys) != 1:
        raise ValueError(f"Invalid variable: {raw_var}")

    name = keys[0]
    var_def = raw_var[name]

    raw_type = var_def["type"]
    if raw_type not in _TYPE_MAP:
        raise ValueError(f"Invalid type: {raw_type}")
    type_ = _TYPE_MAP[raw_type]

    description = var_def.get("description", "")
    required = "default" not in var_def
    default = var_def.get("default", None)

    return Variable(
        name=name,
        description=description,
        type_=type_,
        required=required,
        value=default,
    )


def _parse_tf_vars(
    vars_file: str = os.path.join("terraform", "vars.tf"),
) -> list[Variable]:
    """Parse the terraform variables file."""
    with open(vars_file) as f:
        raw_vars = hcl2.load(f)

    return [_parse_var(v) for v in raw_vars["variable"]]


def _create_function_from_vars(cfg_vars: list[Variable]) -> str:
    """Create a function that accepts the variables as arguments."""
    fn_args = ", ".join(
        [
            f"{v.name}: Optional[{v.type_.cli_type.__name__}] = None"
            for v in sorted(cfg_vars, key=lambda v: v.required)
        ]
    )
    pass_along_args = ", ".join([f"{v.name}={v.name}" for v in cfg_vars])
    return f"""\
from typing import Optional

@cli.command()
def provision(out: Optional[str] = None, {fn_args}):
    s = _cli_provision(cfg_vars, {pass_along_args})
    if out:
        with open(out, 'w') as f:
            f.write(s)
    else:
        print(s)
"""


def _cli_provision(cfg_vars: list[Variable], **kwargs):
    """Actual code to create a Terraform vars file, given input from CLI."""
    max_len = 0

    for v in cfg_vars:
        # Get a value passed from the command line if it's set.
        val = kwargs.get(v.name, None)
        if val is not None:
            v.value = _TYPE_MAP[v.type_.tf_type].parser(val)
        # Auto-generate a value if it's still missing
        if v.value is None and v.name in _AUTO_GENERATE:
            logger.info("Auto-generating value for %s", v.name)
            v.value = _AUTO_GENERATE[v.name]()
        # Prompt for required values
        if v.required and v.value is None:
            # Prompt for input
            raw_val = input(f"{v.name}> ")
            v.value = v.type_.parser(raw_val)
        else:
            # Prompt to confirm the value we have already
            yn_val = ""
            while yn_val not in {"yes", "no", "y", "n"}:
                yn_val = input(f"{v.name} = {v.value} ('yes' to confirm)> ").lower()
            if yn_val.startswith("n"):
                new_raw_val = input(f"{v.name}> ")
                v.value = v.type_.parser(new_raw_val)
        n = len(v.name)
        if n > max_len:
            max_len = n

    # Format the config file
    var_map = {v.name: v for v in cfg_vars}
    return "\n".join(
        [
            f"{v.name:<{max_len}} = {json.dumps(v.value)}"
            for v in cfg_vars
            if _CONDITIONAL.get(v.name, lambda _: True)(var_map)
        ]
    )


def init_provision_cli(cli: typer.Typer):
    """Create a `provision` CLI command dynamically from Terraform."""
    cfg_vars = _parse_tf_vars()
    f = _create_function_from_vars(cfg_vars)
    exec(f, {"cli": cli, "cfg_vars": cfg_vars, "_cli_provision": _cli_provision})
