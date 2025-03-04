# Virtual network `*-rbc-vnet`

This document describes our virtual network configuration and different deployment options associated with it.

## Address Space

**Default Address space:** `10.0.0.0/16`

We reserve the entire `/16` range for our virtual network,
under the assumption that the vnet will be entirely isolated from other networks.
This simplifies our development and lends itself to using a private endpoint for deployment instead of peering.

When necessary, we can reconfigure the address space to accommodate peering;
however, since peering breaks network isolation, we prefer to use a private endpoint instead.
If peering is required, please adjust the variables in `net_vars.tf` to accommodate your network.

Our internal services are all isolated within their own subnets.
Wherever possible, they communicate with each other using private link.

In order to support isolated subnets and private link,
we need to reserve a surprising number of IP addresses -- at least 200.
This makes the  **minimum size needed** for our network currently `/24`.

Our subnets are described in more detail below.

## Subnets
| Name | Default CIDR(s) | Minimum size | Notes |
|--------|--------|--------|--------|
| `default` | `10.0.0.0/24` | `/29` (8) | Reserved for future use. |
| `app` | `10.0.1.0/24` | `/27` (32) | Delegated to Azure Container App |
| `redis` | `10.0.2.0/24` | `/29` (8) | Redis private endpoint |
| `fr` | `10.0.3.0/24` | `/29` (8) | Document Intelligence private endpoint |
| `db` | `10.0.4.0/24` | `/29` (8) | SQL Server private endpoint |
| `openai` | `10.0.5.0./24` | `/29` (8) | Azure OpenAI private endpoint |
| `gateway` | `10.0.6.0/24` | `/29` (8) | App Gateway |
| `gateway-pl` | `10.0.7.0/24` | `/29` (8) | App Gateway private link |
| `fs` | `10.0.8.0/24` | `/27` (32) | File service private endpoint (for research environment persistent storage) |
| `AzureFirewallSubnet`* | `10.0.9.0/24` | `/26` (64) | Firewall for outbound traffic |
| `monitor` | `10.0.10.0/24` | `/29` (8) | Azure Monitor private endpoint |
| `kv` | `10.0.11.0/24` | `/29` (8) | Key Vault private endpoint |

`*` Note the name of the firewall subnet is required by Azure and is not configurable.



#### Reserved IP Addresses

| Name | Subnet | Default value | Notes
|--------|--------|--------|--------|
| `*-rbc-app-gw-feip-priv` | `gateway` | `10.0.6.66` | App Gateway private IP |
| `*-rbc-gateway-ip` | N/A | ? | App Gateway public IP (not known until provisioned) |
| `*-rbc-firewall-ip` | N/A | ? | Firewall public IP (not known until provisioned) |


# Outbound Traffic

We deploy an outbound firewall that will restrict outbound traffic from our vnet.

Outbound traffic from our application is routed through this firewall.

By default, this firewall blocks all traffic _except_ for requests to the following domains, which are exempted in order to allow the application to pull images from the Azure Container Registry:

```
azurecr.io
blindchargingapi.azurecr.io
blindchargingapi.eastus.data.azurecr.io
```

Optionally, you may configure additional allowed destination domains by setting the `firewall_allowed_domains` variable.

Note that if you wish to provide our API with SAS signed links to store redaction results, you will need to exempt the storage account destination(s) in this way.

# Inbound Traffic

By default, we do not connect our application gateway to the internet.
This means there is no way inbound traffic whatsoever from the public internet.

## Private deployment scenario

In the normal (and recommended) private deployment scenario,
we prefer to deploy a private endpoint for our application gateway in the vnet of your choice.
You may then access our API on the private IP of that endpoint.

Another option is to configure vnet peering.
This will require you to coordinate the IP space configuration (as described above).
We do not recommend this option,
as it is more complex to set up and is less secure,
since it exposes our entire vnet to your entire vnet.


## Public deployment scenario

In some cases we might need to deploy the application gateway with a public IP.

In these cases, we _highly_ recommend also deploying a WAF (Web Application Firewall) in front of the gateway.
This will protect the gateway from common web attacks.

In addition, we recommend configuring the gateway to only accept traffic from a specific set of IP addresses,
corresponding to your known clients / network.

## DNS/SSL

By default, we will generate a self-signed certificate for the application gateway.

If you manually trust our self-signed certificate, you can use it as-is with the gateway IP (either the private endpoint IP or the public IP, depending on your deployment).

We recommend setting up a real domain name with an SSL certificate from a trusted authority.

In a public deployment, we can configure the app to generate a certificate automatically using ACME.

For private deployments, you will need to provide your own certificate to us.
