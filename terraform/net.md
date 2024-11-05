# Address Space

By default, the Terraform module provisions a virtual network with a very expansive IP range. We assume that the RBC network is deployed as an isolated service and will not be peered to another network.

If peering is required, we will need to allow the address space to be configured in a way that suits the specific deployment.

The default network configuration is described below.

## Default address space

### Virtual network `*-rbc-vnet`

Address space: `10.0.0.0/16`

Minimum size: `/25`

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

#### Reserved IP Addresses

| Name | Subnet | Default value | Notes
|--------|--------|--------|--------|
| `*-rbc-app-gw-feip-priv` | `gateway` | `10.0.6.66` | App Gateway private IP |
