from collections import defaultdict
from collections.abc import Mapping
from typing import Any


def _format_cost(cost: float, currency: str) -> str:
    prefix = "$" if currency == "USD" else f"{currency} "
    return f"{prefix}{cost:,.6f}"


def format_usage_summary(usage: Mapping[str, Any]) -> str:
    """Format pipeline usage and estimated costs for console output."""
    calls = usage.get("calls", [])
    totals = usage.get("totals", {})

    costs_by_step: dict[tuple[str, str], float] = defaultdict(float)
    currencies = set()
    for call in calls:
        cost_estimate = call.get("cost_estimate")
        if not cost_estimate or cost_estimate.get("estimated_cost") is None:
            continue

        operation = call.get("operation", "unknown")
        currency = cost_estimate.get("currency", "USD")
        currencies.add(currency)
        costs_by_step[(operation, currency)] += cost_estimate["estimated_cost"]

    default_currency = currencies.pop() if len(currencies) == 1 else "USD"
    lines = [
        "Pipeline usage summary",
        f"  Pages processed: {totals.get('pages', 0):,}",
        "  Model tokens:",
        f"    Input: {totals.get('input_tokens', 0):,}",
        f"    Output: {totals.get('output_tokens', 0):,}",
        f"    Cached input: {totals.get('cached_input_tokens', 0):,}",
        f"    Reasoning output: {totals.get('reasoning_output_tokens', 0):,}",
        "  Cost by processing step:",
    ]

    if costs_by_step:
        for (operation, currency), cost in costs_by_step.items():
            lines.append(f"    {operation}: {_format_cost(cost, currency)}")
    else:
        lines.append("    No priced calls")

    total_cost = totals.get("estimated_cost")
    if total_cost is None:
        total_cost = sum(costs_by_step.values())
    lines.append(f"  Total job cost: {_format_cost(total_cost, default_currency)}")
    return "\n".join(lines)


def print_usage_summary(
    usage: Mapping[str, Any] | None, *, enabled: bool = True
) -> None:
    """Print a concise pipeline usage and cost summary."""
    if enabled and usage:
        print(format_usage_summary(usage))
