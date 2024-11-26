# Blind Charging API - Terraform

This directory contains the Terraform configuration to deploy the Blind Charging API on Azure.

## Quick start

Follow these steps to deploy the Blind Charging API.

### 1. Create Azure subscription

You will need the subscription ID for step (2), so first set this up through Azure.

### 2. Log in to Azure via the CLI

If you don't have the Azure CLI installed yet, [set that up](https://learn.microsoft.com/en-us/cli/azure/).

Then, log in with the tenant you used in step (1):

```zsh
# If necessary, set the environment to use GovCloud.
 > az cloud set -n AzureUSGovernment
# Then complete the login flow.
 > az login
```

### 3. Set project variables

Make a new `<my-new-env>.tfvars` file with the relevant values.
(See `./vars.tf` for more information on the available options.)
The Harvard team will need to provision some of these values.

**NOTE** See [the CLI `provision` command](../cli/README.md) for help generating this file.


### 4. Initialize Terraform with Azure backend

If you don't have the Terraform CLI installed yet, [set that up](https://developer.hashicorp.com/terraform/install).

Terraform uses a file called `terraform.tfstate` to track the resources it manages.
We use Azure as a backend to store this information, in a separate long-lived resource group from the other resources we create.

To provision this backend, run the following comand from this directory:

```zsh
./backend/init.sh <my-new-env>.tfvars
```

This creates a file called `./backend/azure.hcl` which will point Terraform to the Azure backend.

Now initialize Terraform:

```zsh
terraform init -backend-config="backend/azure.hcl"
```

#### Common errors

Sometimes you will see a permission error on the key vault.
First, try to re-run the `./backend/init.sh` command and see if the permissions just needed more time to propagate.
If that doesn't work, you can manually grant yourself permission on the key vault through the Azure Portal.
Look for the newly created KeyVault in the new `tfstate` resource group, and give yourself Key Vault Administrator permissions on this resource. Then, re-run the `./backend/init.sh` command.

### 5. Now deploy the application

To initialize the Terraform environment. Then, to deploy the application, run:

```zsh
terraform plan -var-file="<my-new-env>.tfvars" -out="deploy.tfplan"
terraform apply "deploy.tfplan"
```

#### Common errors

**Container App Resource Registration** Sometimes you will see an error that the Container App resource provider is not registered. You can register the Container App resource in your subscription with the Azure CLI with the command:

```
az provider register --namespace Microsoft.App
```
More info on this issue [here](https://learn.microsoft.com/en-us/azure/azure-resource-manager/management/resource-providers-and-types).

#### Making subsequent updates

If you are running commands from the same environment,
you generally don't need to run the initialization steps in (4).

Any time you run in a new environment, start by initializing as in step (4).

With the environment initialized, you can update the environment in the same way as you deployed in the first place:

```zsh
terraform plan -var-file="<my-new-env>.tfvars" -out="update.tfplan"
terraform apply -out="update.tfplan"
```

### Appendix I. Applying new docker image updates

**NOTE** These instructions are liable to change in the future as we automate more deployment steps!

Updates that affect the running service such as `app_config` changes or Docker image updates are _not_ rolled out automatically at this time.

You will need to restart the container app revision in order to pick up these new changes.

You can do this either in the Azure Portal UI or on the command line.

The CLI steps (assuming you have `az` and `jq` installed) are:

```zsh
RBC_CONTAINER_APP_NAME=`az containerapp list | jq --raw-output '.[0].name'`
RBC_CONTAINER_APP_ACTIVE_REVISION_NAME=`az containerapp revision list -n "$RBC_CONTAINER_APP_NAME" -g RaceBlindCharging | jq --raw-output '.[0].name'`
az containerapp revision restart --revision "$RBC_CONTAINER_APP_ACTIVE_REVISION_NAME" -g RaceBlindCharging
```

### Appendix II. Managing multiple environments

It's common to want multiple environments, such as development / staging and production.

There is more than one way to manage multiple environments with Terraform.
The way we suggest here is to use `terraform workspace`,
which will let you manage all of your environments easily within the same subscription.

> [!TIP]
> If you _cannot_ use the same subscription for all of your environments,
> you will need to create an entirely new backend for your new environment.
>
> We do not recommend this.

#### 1. Create a new `tfvars` file

Create a new `<my-other-env>.tfvars` file representing the configuration you want to use for this environment.
Many parameters will end up being the same as your original `tfvars` file.
This is actually a _good_ thing, since the closer you can make your test environment to the production environment, the better.

> [!CAUTION]
>  - The `partner` key must be _identical_ in all of your `tfvars` files.
>  - The `subscription` key must be _identical_ in all of your `tfvars` files.
>  - The `registry_password` key can be _identical_ in all of your `tfvars` files. If you would like a new token for different environments, please contact us.
>  - The `tfstate_resource_group` should only exist in the `tfvars` you used to initialize the backend in step (4). All of your environments will share the same backend storage for their state, so you do _not_ need to define the `tfstate_resource_group` in all files.
>  - The resource group keys `resource_group_name` and `app_infra_resource_group_name` need to be _unique_ in each tfvars file.
>  - If you are using custom name variables, it is a good idea to ensure the names are unique. Azure requires some names to be globally unique, so you might encounter errors if you try to re-use names.

#### 2. Create a new `workspace`

Now create a new workspace for your new environment:

```
terraform workspace new <my-new-env>
```

You can call your new workspace whatever you want. For example, `terraform workspace new prod` will create a new workspace named `prod`.

> [!TIP]
> If you already set up an environment without an explicit workspace, it will be called `default`.
>
> You can see which workspaces you have available with the command `terraform workspace list`.

#### 3. Create the new resources

You can follow step (5) from the main README above to deploy new resources to this workspace.

The new workspace will have an empty state file,
so when you run `terraform plan` and `terraform apply` it will create all the new resources instead of modifying resources in your old workspace.

> [!IMPORTANT]
> Remember to reference the correct `tfvars` file when you run these commands.
> (I.e., when you are in the `prod` workspace, you will want to use the `prod.tfvars` file, _not_ the `dev.tfvars` file.)

#### 4. Switching between workspaces

You will often need to switch back and forth between workspaces.

Here are some useful commands for working with workspaces:

```bash
# List all available workspaces
terraform workspace list

# Show the active workspace
terraform workspace show

# Switch to a new workspace
terraform workspace select <name>
```

Always make sure you have activated the correct workspace and are using the correct tfvars file before applying any new changes to the infrastructure.
