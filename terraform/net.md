# Address Space

By default, the Terraform module provisions a virtual network with a very expansive IP range. We assume that the RBC network is deployed as an isolated service and will not be peered to another network.

If peering is required, we will need to allow the address space to be configured in a way that suits the specific deployment.

The default network configuration is described below.

## Default address space

### Virtual network `*-rbc-vnet`

Default Address space: `10.0.0.0/16`

By default, we do *not* attempt to use an optimal subnet configuration.
We instead reserve an expansive `/16` range and give services their own `/24` range within that.
This simplifies development and usually does not matter since our vnet is completely isolated.

When using peering, it may be necessary to reconfigure our vnet to be much more efficient.

The **minimum size needed** for our network is currently `/24`.
We currently reserve a minimum of 184 addresses, as described below.

#### Subnets
| Name | Default CIDR(s) | Minimum size | Notes |
|--------|--------|--------|--------|
| `default` | `10.0.0.0/24` | `/29` | Reserved for future use. |
| `app` | `10.0.1.0/24` | `/27` | Delegated to Azure Container App |
| `redis` | `10.0.2.0/24` | `/29` | Redis private endpoint |
| `fr` | `10.0.3.0/24` | `/29` | Document Intelligence private endpoint |
| `db` | `10.0.4.0/24` | `/29` | SQL Server private endpoint |
| `openai` | `10.0.5.0./24` | `/29` | Azure OpenAI private endpoint |
| `gateway` | `10.0.6.0/24` | `/29` | App Gateway |
| `gateway-pl` | `10.0.7.0/24` | `/29` | App Gateway private link |
| `fs` | `10.0.8.0/24` | `/27` | File service private endpoint (for research environment persistent storage) |
| `AzureFirewallSubnet`* | `10.0.9.0/24` | `/26` | Firewall for outbound traffic |
| `monitor` | `10.0.10.0/24` | `/29` | Azure Monitor private endpoint |

`*` Note the name of the firewall subnet is required by Azure and is not configurable.



#### Reserved IP Addresses

| Name | Subnet | Default value | Notes
|--------|--------|--------|--------|
| `*-rbc-app-gw-feip-priv` | `gateway` | `10.0.6.66` | App Gateway private IP |
| `*-rbc-gateway-ip` | N/A | ? | App Gateway public IP (not known until provisioned) |
| `*-rbc-firewall-ip` | N/A | ? | Firewall public IP (not known until provisioned) |

# Outbound Firewall

Outbound traffic for all subnets (excluding the Gateway, which is required to hop directly to the internet) is routed through the firewall.

The firewall denies all traffic except for domains explicitly configured in the allow list.
By default, we only exempt the domains required to pull from our container registry to fetch the API image:

```
azurecr.io
blindchargingapi.azurecr.io
blindchargingapi.eastus.data.azurecr.io
```

Optionally, additional domains can be exempted by setting the `firewall_allowed_domains` variable.

Note that it is necessary to exempt domains in order to use SAS signed links for redaction results.

# Web Application Firewall

When the app is exposed to the public internet, inbound traffic is controlled through the Web Application Firewall.

By default, traffic is not filtered by IP. We recommend restricting traffic to specific IPs using the `var.allowed_ips` setting.
