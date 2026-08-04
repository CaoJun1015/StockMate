"""Tax-aware pricing calculations without database dependencies."""

from __future__ import annotations


def calc_tax_adjusted_profit(
    purchase_price: float,
    quote_price: float,
    quantity: int,
    tax_rate: float | None,
    purchase_tax_inclusive: bool,
    quote_tax_inclusive: bool,
) -> float:
    if not tax_rate:
        return (quote_price - purchase_price) * quantity
    purchase_exclusive = (
        purchase_price / (1 + tax_rate)
        if purchase_tax_inclusive
        else purchase_price
    )
    quote_exclusive = (
        quote_price / (1 + tax_rate) if quote_tax_inclusive else quote_price
    )
    return (quote_exclusive - purchase_exclusive) * quantity
