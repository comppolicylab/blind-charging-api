Blind Charging API Server
===

## Code generation

All of the code in `./generated` is generated from code in `./schema`.

To run code generation, make sure you have the `fastapi-codegen` repo cloned.

**Note 1** [Joe's fork of the repo](https://github.com/jnu/fastapi-code-generator)
still has a couple more useful features that haven't been merged into upstream branch.

**Note 2** FastAPI code gen uses `poetry` for package management, so you will need to install that and use it to run the code generator.

```zsh
# Set this to the path where this repo is checked out.
BCAPI_ROOT=../../comppolicylab/blind-charging-api
poetry run python -m fastapi_code_generator -i "$BCAPI_ROOT/app/schema/openapi.yaml" -o "$BCAPI_ROOT/app/server/generated" -r -t "$BCAPI_ROOT/app/schema/templates" -d pydantic_v2.BaseModel -p 3.13
```

### Implementations

Code generation takes care of stubs for the API routes.
To write the implementations,
add corresponding functions in `./handlers`.

## Database

### Driver

Install the MS ODBC driver for your operating system, for example using these instructions:

https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/install-microsoft-odbc-driver-sql-server-macos?view=sql-server-ver16

### Start a dev database

You can develop against a MS SQL database running in Docker:

```bash
docker run \
    --platform linux/amd64 \
    --name blind-charging-mssql \
    -e "ACCEPT_EULA=Y" \
    -e "MSSQL_SA_PASSWORD=bc2Password" \
    -p 1433:1433 \
    -d mcr.microsoft.com/mssql/server:2022-latest
```
