Command line tools for the Blind Charging API
===

## `provision`

```
poetry run python -m cli provision
```

Simplifies generating `tfvars` files.

See `terraform/vars.tf` for information about available settings.
You may pass these parameters in the CLI.
Any required parameter not passed in the CLI will be prompted interactively.

Use the `--out` parameter to specify a file path to write to.
Otherwise the result will print to stdout.

## `create-client <name>`

```
poetry run python -m cli create-client <name>
```

Create an `OAuth2` client ID and client secret pair.
