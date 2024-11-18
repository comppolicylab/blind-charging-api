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
# This means anything on the line after #, //, or /*.
# NOTE That this is *not* perfect parsing! For example, if a line is
# commented out with a multiline comment like this:
# /*
#   variable = "value"
# */
# Then the variable will still be loaded.
VARS=$(cat $1 | sed 's/\/\/.*//;s/\/\*.*//;s/#.*//' | grep -E '^\s*(location|partner|subscription_id|tfstate_resource_group)\s*=' | sed 's/ *= */=/')
eval "$VARS"
# Green
tput setaf 2
echo "Loaded variables from $1"
tput sgr0

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
KEYVAULT_NAME=$_CLEAN_PARTNER'rbctfkv'
KV_STORAGE_KEY_NAME="terraform-backend-key"

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
  az keyvault create --name $KEYVAULT_NAME --resource-group $tfstate_resource_group --location $location --enabled-for-deployment true --enabled-for-template-deployment true --enabled-for-disk-encryption true --enabled-for-deployment true --enabled-for-template-deployment true --enabled-for-disk-encryption true

# Create a role assignment for the user to access the key vault if necessary
ROLES=`az role assignment list --role "Key Vault Secrets Officer" --assignee "$UPN" --scope $(az keyvault show --name $KEYVAULT_NAME --resource-group $tfstate_resource_group --query id -o tsv)`
# ROLES will be `[]` if the user doesn't have the role assignment.
if [ "$ROLES" == "[]" ]; then
  az role assignment create --role "Key Vault Secrets Officer" --assignee "$UPN" --scope $(az keyvault show --name $KEYVAULT_NAME --resource-group $tfstate_resource_group --query id -o tsv)
fi

# The role assignment takes time to propagate, so wait til it's ready.
done=false
while [ $done == false ]; do
  ROLES=`az role assignment list --role "Key Vault Secrets Officer" --assignee "$UPN" --scope $(az keyvault show --name $KEYVAULT_NAME --resource-group $tfstate_resource_group --query id -o tsv)`
  if [ "$ROLES" != "[]" ]; then
    done=true
    # Green
    tput setaf 2
    echo "Verified role assignment."
    tput sgr0
  else
    # Cyan
    tput setaf 6
    echo "Waiting for role assignment to propagate..."
    tput sgr0
    sleep 2
  fi
done

# Create storage account if it doesn't exist already
az storage account show --name $STORAGE_ACCOUNT --resource-group $tfstate_resource_group &> /dev/null || \
  az storage account create --name $STORAGE_ACCOUNT --resource-group $tfstate_resource_group --location $location --sku Standard_LRS --encryption-services blob

# Set the storage account key in the key vault if it doesn't exist
az keyvault secret show --name $KV_STORAGE_KEY_NAME --vault-name $KEYVAULT_NAME --query value &> /dev/null || \
  az keyvault secret set --name $KV_STORAGE_KEY_NAME --vault-name $KEYVAULT_NAME --value $(az storage account keys list --account-name $STORAGE_ACCOUNT --resource-group $tfstate_resource_group --query '[0].value' -o tsv) > /dev/null

# Create the container if it doesn't exist
az storage container show --name $CONTAINER_NAME --account-name $STORAGE_ACCOUNT &> /dev/null || \
  az storage container create --name $CONTAINER_NAME --account-name $STORAGE_ACCOUNT

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
BACKEND_FILE="$BASEDIR/azure.hcl"
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
  terraform init -backend-config="$BACKEND_FILE"
else
  # Yellow
  tput setaf 3
  echo "Ok, we won't initialize Terraform for you."
  echo
  echo "You can initialize Terraform yourself with the following command:"
  tput sgr0
  echo
  echo "terraform init -backend-config=\"$BACKEND_FILE\""
fi
