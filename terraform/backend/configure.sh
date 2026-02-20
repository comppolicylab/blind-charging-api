#!/usr/bin/env bash
set -e

# Find the tfvars blob.
STORAGE_ACCT=$(az storage account list --query "[?ends_with(name, 'rbctfvars')].name" --output tsv)

# Download the tfvars blob to the current directory.
az storage blob download \
  --account-name "$STORAGE_ACCT" \
  --container-name tfvars \
  --name terraform.tfvars \
  --file ./terraform.tfvars
