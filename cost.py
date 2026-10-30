"""Rough per-request cost estimate, USD per 1M tokens (verify against each provider's price page)."""

PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "openai/gpt-oss-120b": (0.0, 0.0),  # Groq free plan: rate limits instead of a price
    "openai/gpt-oss-20b": (0.0, 0.0),
    "us.amazon.nova-lite-v1:0": (0.06, 0.24),
    "amazon.nova-lite-v1:0": (0.06, 0.24),
}
DEFAULT_PRICE = (2.00, 10.00)  # unknown model: assume Sonnet pricing so the estimate errs high


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, DEFAULT_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000
