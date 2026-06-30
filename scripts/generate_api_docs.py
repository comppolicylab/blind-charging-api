"""Generate static API documentation for GitHub Pages."""

import subprocess
import tomllib
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = ROOT / "pyproject.toml"
SCHEMA_PATH = ROOT / "app" / "schema" / "openapi.yaml"
DOCS_DIR = ROOT / "docs"
DOCS_SCHEMA_PATH = DOCS_DIR / "openapi.yaml"
INDEX_PATH = DOCS_DIR / "index.html"
NOJEKYLL_PATH = DOCS_DIR / ".nojekyll"


INDEX_HTML = dedent(
    """\
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <title>Blind Charging API Documentation</title>
        <style>
          body {
            margin: 0;
            padding: 0;
          }
        </style>
      </head>
      <body>
        <redoc spec-url="./openapi.yaml"></redoc>
        <script src="https://cdn.redoc.ly/redoc/latest/bundles/redoc.standalone.js"></script>
      </body>
    </html>
    """
)


def get_app_version() -> str:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return pyproject["project"]["version"]


def git_output(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True,
        check=check,
        text=True,
    )
    return result.stdout.strip()


def get_git_reference() -> str:
    tag = git_output("describe", "--exact-match", "--tags", "HEAD", check=False)
    is_dirty = bool(git_output("status", "--short"))
    dirty_suffix = " (dirty)" if is_dirty else ""

    if tag:
        return f"git tag {tag}{dirty_suffix}"

    sha = git_output("rev-parse", "--short", "HEAD")
    return f"git sha {sha}{dirty_suffix}"


def update_schema_for_docs(
    schema_text: str,
    app_version: str,
    git_reference: str,
) -> str:
    lines = schema_text.splitlines(keepends=True)
    in_info = False
    in_description = False
    schema_version = None
    added_git_reference = False

    for index, line in enumerate(lines):
        current_index = index
        stripped_line = line.strip()

        if stripped_line == "info:" and not line.startswith(" "):
            in_info = True
            continue

        if in_info and stripped_line and not line.startswith(" "):
            break

        if in_description and line.startswith("  ") and not line.startswith("    "):
            lines.insert(index, f"    Generated from {git_reference}.\n")
            added_git_reference = True
            in_description = False
            current_index = index + 1

        if in_info and stripped_line == "description: |":
            in_description = True
            continue

        if in_info and line.startswith("  version:"):
            schema_version = line.split(":", maxsplit=1)[1].strip().strip("'\"")
            line_ending = "\n" if line.endswith("\n") else ""
            composite_version = f"api-v{app_version};schema-v{schema_version}"
            lines[current_index] = f"  version: {composite_version}{line_ending}"

        if schema_version is not None and added_git_reference:
            break

    if schema_version is None:
        raise ValueError("OpenAPI schema is missing info.version")
    if not added_git_reference:
        raise ValueError("OpenAPI schema is missing info.description")

    return "".join(lines)


def main() -> None:
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"OpenAPI schema not found: {SCHEMA_PATH}")

    DOCS_DIR.mkdir(exist_ok=True)
    schema_text = SCHEMA_PATH.read_text(encoding="utf-8")
    DOCS_SCHEMA_PATH.write_text(
        update_schema_for_docs(schema_text, get_app_version(), get_git_reference()),
        encoding="utf-8",
    )
    INDEX_PATH.write_text(INDEX_HTML, encoding="utf-8")
    NOJEKYLL_PATH.touch()

    print(f"Wrote API docs to {DOCS_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
