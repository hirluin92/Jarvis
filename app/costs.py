# USD per million tokens (input, output), Anthropic first-party API rates.
# Haiku 5.5 prices apply to prompts up to 100K tokens, which is all Jarvis sends.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-5-5": (0.10, 0.50),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated cost in USD, or None when the model has no known price (e.g. the test fake)."""
    prices = PRICES_USD_PER_MTOK.get(model)
    if prices is None:
        return None
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


def format_cost(cost: float | None) -> str:
    return "n/d" if cost is None else f"${cost:.4f}"
