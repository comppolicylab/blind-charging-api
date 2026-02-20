#!/usr/bin/env bash
set -e

# Find the tfvars blob.
STORAGE_ACCT=$(az storage account list --query "[?ends_with(name, 'rbctfvars')].name" --output tsv)

# Get the name of the current workspace.
# If the workspace is not default, we generate a suffix like `env:<workspace>`.
WORKSPACE=$(terraform workspace show)
if [ "$WORKSPACE" != "default" ]; then
  WORKSPACE_SUFFIX="env:$WORKSPACE"
else
  WORKSPACE_SUFFIX=""
fi

# Download the tfvars blob to the current directory.
az storage blob download \
  --account-name "$STORAGE_ACCT" \
  --container-name tfvars \
  --name "terraform.tfvars$WORKSPACE_SUFFIX" \
  --file ./terraform.tfvars

# Cyan
tput setaf 6
echo "Fetched terraform.tfvars$WORKSPACE_SUFFIX and saved to ./terraform.tfvars"
tput sgr0
