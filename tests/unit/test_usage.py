from app.server.tasks.usage import format_usage_summary


def test_format_usage_summary():
    usage = {
        "calls": [
            {
                "operation": "analyze:azuredi",
                "cost_estimate": {
                    "estimated_cost": 0.0075,
                    "currency": "USD",
                },
            },
            {
                "operation": "redact:openai",
                "cost_estimate": {
                    "estimated_cost": 0.009918,
                    "currency": "USD",
                },
            },
            {
                "operation": "redact:openai",
                "cost_estimate": {
                    "estimated_cost": 0.002,
                    "currency": "USD",
                },
            },
        ],
        "totals": {
            "pages": 5,
            "input_tokens": 8524,
            "output_tokens": 1302,
            "cached_input_tokens": 3072,
            "reasoning_output_tokens": 0,
            "estimated_cost": 0.019418,
        },
    }

    assert format_usage_summary(usage) == "\n".join(
        [
            "Pipeline usage summary",
            "  Pages processed: 5",
            "  Model tokens:",
            "    Input: 8,524",
            "    Output: 1,302",
            "    Cached input: 3,072",
            "    Reasoning output: 0",
            "  Cost by processing step:",
            "    analyze:azuredi: $0.007500",
            "    redact:openai: $0.011918",
            "  Total job cost: $0.019418",
        ]
    )


def test_format_usage_summary_without_cost_estimates():
    usage = {"calls": [], "totals": {"pages": 1}}

    summary = format_usage_summary(usage)

    assert "    No priced calls" in summary
    assert "  Total job cost: $0.000000" in summary
