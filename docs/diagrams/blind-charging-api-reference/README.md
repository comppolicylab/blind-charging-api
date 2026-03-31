# blind-charging-api Reference Diagrams

This folder contains a parallel diagram set for the actual `blind-charging-api` project in this repo.

## What these are for

These diagrams are intended as a reference point for understanding the current inspiration project on its own terms, separate from the proposed SCC PDO evidence review concept.

## How this set was derived

- The API shape and asynchronous workflow come from:
  - `/Users/alexcw/Development/blind-charging-api/README.md`
  - `/Users/alexcw/Development/blind-charging-api/app/server/tasks/`
  - `/Users/alexcw/Development/blind-charging-api/app/server/handlers/redaction.py`
- The deployment and network shape come from:
  - `/Users/alexcw/Development/blind-charging-api/terraform/`
  - `/Users/alexcw/Development/blind-charging-api/docs/diagrams/RBC Private On-Prem.drawio`

## Interpretation notes

- This project is shown as API-first, not as a staff-facing portal.
- The system/network diagram uses the current repo's actual Azure product choices, including Application Gateway and Container Apps.
- The research environment is shown only as an optional note because it is present in Terraform but not central to the main redaction flow.

## Files

- `data-flow.mmd` / `data-flow.png` / `data-flow.svg`
- `system-network.mmd` / `system-network.png` / `system-network.svg`
- `functional.mmd` / `functional.png` / `functional.svg`

## Render commands

```bash
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/data-flow.mmd -o docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/data-flow.png -w 2200 -b white
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/data-flow.mmd -o docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/data-flow.svg -w 2200 -b white
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/system-network.mmd -o docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/system-network.png -w 2400 -b white
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/system-network.mmd -o docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/system-network.svg -w 2400 -b white
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/functional.mmd -o docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/functional.png -w 2200 -b white
npx -y @mermaid-js/mermaid-cli -i docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/functional.mmd -o docs/diagrams/scc-pdo-evidence-review/blind-charging-api-reference/functional.svg -w 2200 -b white
```
