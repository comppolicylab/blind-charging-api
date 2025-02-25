#!/usr/bin/env bash
set -e

usage () {
cat << EOF
Usage: $0 <path-to-tfvars-file>

This script initializes the Terraform state store in Azure.

The remote state store is used to store the Terraform state file in Azure Blob Storage.

Arguments:
  <path-to-tfvars-file>  The path to the .tfvars file used for your Blind Charging deployment.
EOF
}

# Initialize the Terraform state store in Azure.
# Modified from https://learn.microsoft.com/en-us/azure/developer/terraform/store-state-in-azure-storage?tabs=azure-cli

# Make sure the Azure CLI is installed.
if ! command -v az &> /dev/null; then
  # Red
  tput setaf 1
  echo "Azure CLI is not installed. Please install the Azure CLI before running this script."
  tput sgr0
  exit 1
fi

# The first argument in the script should be the path to the .tfvars file.
# If the argument is not provided, print the usage and exit.
if [ -z "$1" ]; then
  # Yellow
  tput setaf 3
  usage
  tput sgr0
  exit 1
fi

# Check if the .tfvars file exists.
if [ ! -f "$1" ]; then
  # Red
  tput setaf 1
  echo "The .tfvars file does not exist. Please provide the path to the .tfvars file."
  tput sgr0
  exit 1
fi

# Load the .tfvars file.
# Strip out anything on a line that looks like a comment.
# This means anything on the line after #, //, or /* ... */
# (Note the last is multiline.)
# Use perl to do this with a multiline pattern, then leave only valid lines in the var $CFG.
CFG=$(perl -0777 -pe 's/\/\*.*?\*\///gs' $1 | perl -pe 's/(\/\/|#).*?$//mg')

# Load a few specific inline variables from the config.
# Eval them to set them as shell variables.
VARS=$(echo "$CFG" | grep -E '^\s*(location|partner|subscription_id|tfstate_resource_group)\s*=' | sed 's/ *= */=/')
eval "$VARS"
# Green
tput setaf 2
echo "Loaded variables from $1"
tput sgr0

# The `tags` variable is defined as a hash like this:
# tags = {
#   "key1": "value1",
#   "key2": "value2"
# }
# We need to convert this to a string like this:
# key1=value1 key2=value2
# This is because the Azure CLI expects tags in this format.

# First, extract the tags hash from the .tfvars file.
# We can use a quick Perl command to extract the tags hash.
TAGS=$(echo $CFG | perl -0777 -ne 'print "$1" if /tags\s*=\s*({[^}]*})/s' | jq -r 'to_entries | map("\(.key)=\(.value)") | join(" ")')

# Check if the `tfstate_resource_group` variable is set.
if [ -z "$tfstate_resource_group" ]; then
  # Red
  tput setaf 1
  echo "The tfstate_resource_group variable is not set in the .tfvars file."
  tput sgr0
  exit 1
fi

# Create the storage account name in the format `rbc-<partner>-tfstate`.
# This must be globally unique.
_CLEAN_PARTNER=$(echo $partner | sed 's/-//g' | awk '{print tolower($0)}')
STORAGE_ACCOUNT=$_CLEAN_PARTNER'rbctfstate'
# Container name is not configurable right now.
CONTAINER_NAME="tfstate"
# Key vault name is not configurable right now.
KEYVAULT_NAME=$_CLEAN_PARTNER'rbctfkvhw'
OLD_KEYVAULT_NAME=$_CLEAN_PARTNER'rbctfkv'
KV_STORAGE_KEY_NAME="terraform-backend-key"
KV_ENCRYPTION_KEY_NAME=$_CLEAN_PARTNER'-rbc-tfstate-encryption-key-hsm'

# Yellow
tput setaf 3
echo "Initializing Terraform state storage in Azure if necessary ..."
tput sgr0

# Ensure that we're in the correct subscription.
az account set --subscription $subscription_id

# Create the resource group if it doesn't exist.
az group show --name $tfstate_resource_group &> /dev/null || \
  az group create --name $tfstate_resource_group --location $location

UPN=$(az account show --query user.name -o tsv)
# Create the key vault if it doesn't exist
az keyvault show --name $KEYVAULT_NAME --resource-group $tfstate_resource_group &> /dev/null || \
  az keyvault create --name $KEYVAULT_NAME --resource-group $tfstate_resource_group --location $location \
  --enabled-for-deployment true \
  --enabled-for-template-deployment true \
  --enabled-for-disk-encryption true \
  --enabled-for-deployment true \
  --enabled-for-template-deployment true \
  --enable-purge-protection true \
  --retention-days 90 \
  --enable-rbac-authorization false \
  --sku "premium" \
  --tags "$TAGS"

# Ensure purge protection is enabled (for keyvaults created with older version of script)
az keyvault update --name $KEYVAULT_NAME --resource-group $tfstate_resource_group \
  --enable-purge-protection true \
  --retention-days 90 \
  --enable-rbac-authorization false

# Ensure current user has create key access to vault
az keyvault set-policy --name $KEYVAULT_NAME --resource-group $tfstate_resource_group \
  --upn $UPN \
  --key-permissions create get list setrotationpolicy update delete \
  --secret-permissions get list set delete


tput setaf 3
echo "Creating encryption key in the keyvault if necessary ..."
tput sgr0


# Create an encryption key in the key vault if it doesn't exist
az keyvault key show --name "$KV_ENCRYPTION_KEY_NAME" --vault-name $KEYVAULT_NAME &> /dev/null || \
  az keyvault key create \
    --name "$KV_ENCRYPTION_KEY_NAME" \
    --vault-name $KEYVAULT_NAME \
    --kty RSA \
    --size 2048 \
    --protection hsm \
    --ops sign verify encrypt decrypt wrapKey unwrapKey \
    --not-before $(date -u '+%Y-%m-%dT%H:%M:%SZ') \
    --tags "$TAGS"

# Ensure the rotation policy is correct
az keyvault key rotation-policy update --name "$KV_ENCRYPTION_KEY_NAME" --vault-name $KEYVAULT_NAME --value @- <<EOF
{
  "lifetimeActions": [
    {
      "action": {
        "type": "Rotate"
      },
      "trigger": {
        "timeAfterCreate": "P90D",
        "timeBeforeExpiry": null
      }
    },
    {
      "action": {
        "type": "Notify"
      },
      "trigger": {
        "timeBeforeExpiry": "P30D"
      }
    }
  ],
  "attributes": {
    "expiryTime": "P2Y"
  }
}
EOF


# Create storage account if it doesn't exist already
az storage account show --name $STORAGE_ACCOUNT --resource-group $tfstate_resource_group &> /dev/null || \
  az storage account create --name $STORAGE_ACCOUNT --resource-group $tfstate_resource_group --location $location --sku Standard_LRS --tags "$TAGS"

# Ensure the account uses a system-assigned identity
az storage account update --name $STORAGE_ACCOUNT --resource-group $tfstate_resource_group --identity-type SystemAssigned

# Ensure the access policy is set so that the storage account can access the key vault
STORAGE_ACCOUNT_PRINCIPAL_ID=$(az storage account show --name $STORAGE_ACCOUNT --resource-group $tfstate_resource_group --query 'identity.principalId' -o tsv)
az keyvault set-policy --name $KEYVAULT_NAME --resource-group $tfstate_resource_group \
  --object-id $STORAGE_ACCOUNT_PRINCIPAL_ID \
  --key-permissions get wrapKey unwrapKey

# Ensure that encryption via user-managed key is configured on the account
KEYVAULT_URI=$(az keyvault show --name $KEYVAULT_NAME --resource-group $tfstate_resource_group --query properties.vaultUri -o tsv)
az storage account update --name $STORAGE_ACCOUNT --resource-group $tfstate_resource_group \
  --encryption-key-name "$KV_ENCRYPTION_KEY_NAME" \
  --encryption-key-source "Microsoft.Keyvault" \
  --encryption-key-vault "$KEYVAULT_URI" \
  --encryption-services blob queue table file \


# Set the storage account key in the key vault if it doesn't exist
az keyvault secret show --name $KV_STORAGE_KEY_NAME --vault-name $KEYVAULT_NAME --query value &> /dev/null || \
  az keyvault secret set --name $KV_STORAGE_KEY_NAME --vault-name $KEYVAULT_NAME --value $(az storage account keys list --account-name $STORAGE_ACCOUNT --resource-group $tfstate_resource_group --query '[0].value' -o tsv) > /dev/null


# Try to delete the old keyvault if it exists. This keyvault worked for GovCloud, but it uses
# the standard SKU which doesn't support HSM keys, which are required for deployment on
# Azure Commercial cloud.
if ( az keyvault show --name $OLD_KEYVAULT_NAME --resource-group $tfstate_resource_group &> /dev/null ); then
  tput setaf 3
  echo "Found an outdated keyvault, deleting it ..."
  tput sgr0

  az keyvault delete --name $OLD_KEYVAULT_NAME --resource-group $tfstate_resource_group
fi


# Create the container if it doesn't exist
az storage container show --name $CONTAINER_NAME --account-name $STORAGE_ACCOUNT &> /dev/null || \
  az storage container create --name $CONTAINER_NAME --account-name $STORAGE_ACCOUNT --tags "$TAGS"

# Check what environment Azure is in (GovCloud, Commercial, etc.)
AZURE_ENVIRONMENT=$(az cloud show --query name -o tsv)
# Translate the Azure environment to a Terraform environment.
# Terraform uses the keys "public," "usgovernment," "german," and "china."
case $AZURE_ENVIRONMENT in
  "AzureUSGovernment")
    ARM_ENVIRONMENT="usgovernment"
    ;;
  "AzureGermanCloud")
    ARM_ENVIRONMENT="german"
    ;;
  "AzureChinaCloud")
    ARM_ENVIRONMENT="china"
    ;;
  *)
    ARM_ENVIRONMENT="public"
    ;;
esac

# Try to register the Azure Container App provider
echo "Enabling container app provider on subscription ..."
az provider register --namespace Microsoft.App

# Green
tput setaf 2
echo "The Terraform state store has been initialized in Azure."
tput sgr0
echo
# Cyan
tput setaf 6
echo "Resource group: $tfstate_resource_group"
echo "Storage account: $STORAGE_ACCOUNT"
echo "Container name: $CONTAINER_NAME"
echo "Key vault name: $KEYVAULT_NAME"
echo "Access granted to: $UPN"
echo "Azure environment: $ARM_ENVIRONMENT"
tput sgr0

# Get the storage account key from the keyvault.
export ARM_ACCESS_KEY=$(az keyvault secret show --name $KV_STORAGE_KEY_NAME --vault-name $KEYVAULT_NAME --query value -o tsv)

# Output an AzureRM backend HCL block for the Terraform configuration
# in the same directory as this script.
BASEDIR=$(dirname "$0")
BACKEND_FILE="$BASEDIR/$_CLEAN_PARTNER.azure.hcl"
cat << EOF > "$BACKEND_FILE"
resource_group_name  = "$tfstate_resource_group"
storage_account_name = "$STORAGE_ACCOUNT"
container_name       = "$CONTAINER_NAME"
key                  = "terraform.tfstate"
access_key           = "$ARM_ACCESS_KEY"
environment          = "$ARM_ENVIRONMENT"
EOF

echo
# Switch color to green
tput setaf 2
echo "The AzureRM backend configuration has been written to $BACKEND_FILE."
tput sgr0
echo
# Let user opt into continuing.
# Yellow
tput setaf 3
read -p "Do you want to initialize Terraform now? (yes/no): " -r
tput sgr0
echo
if [[ $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
  terraform init -backend-config="$BACKEND_FILE" -reconfigure
else
  # Yellow
  tput setaf 3
  echo "Ok, we won't initialize Terraform for you."
  echo
  echo "You can initialize Terraform yourself with the following command:"
  tput sgr0
  echo
  echo "terraform init -backend-config=\"$BACKEND_FILE\" -reconfigure"
fi
