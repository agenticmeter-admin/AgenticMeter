"""Token -> dollar cost. Prices are illustrative (USD per 1M tokens) and MUST be
kept current; expose configure(prices=...) so users can override without a release.
"""
from __future__ import annotations

# (prompt_per_million, completion_per_million)
_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o":            (2.50, 10.00),
    "gpt-4o-mini":       (0.15,  0.60),
    "claude-opus-4":     (15.00, 75.00),
    "claude-sonnet-4":   (3.00, 15.00),
    "claude-haiku-4":    (0.80,  4.00),
}


def set_prices(prices: dict[str, tuple[float, float]]) -> None:
    _PRICES.update(prices)


def _match(model: str | None) -> tuple[float, float] | None:
    if not model:
        return None
    if model in _PRICES:
        return _PRICES[model]
    for key, price in _PRICES.items():       # prefix match: "gpt-4o-2024-.." -> "gpt-4o"
        if model.startswith(key):
            return price
    return None


def cost_usd(model: str | None, prompt_tokens: int | None,
             completion_tokens: int | None) -> float | None:
    price = _match(model)
    if price is None:
        return None
    pin, pout = price
    p = (prompt_tokens or 0) / 1_000_000 * pin
    c = (completion_tokens or 0) / 1_000_000 * pout
    return round(p + c, 6)
