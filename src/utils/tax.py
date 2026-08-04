"""Tax-aware pricing calculations without database dependencies."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def calc_tax_adjusted_profit_cents(
    purchase_price_cents: int,
    quote_price_cents: int,
    quantity: int,
    tax_rate: float | None,
    purchase_tax_inclusive: bool,
    quote_tax_inclusive: bool,
) -> int:
    purchase = Decimal(purchase_price_cents)
    quote = Decimal(quote_price_cents)
    if not tax_rate:
        return (quote_price_cents - purchase_price_cents) * quantity
    divisor = Decimal("1") + Decimal(str(tax_rate))
    purchase_exclusive = (
        purchase / divisor
        if purchase_tax_inclusive
        else purchase
    )
    quote_exclusive = (
        quote / divisor if quote_tax_inclusive else quote
    )
    value = (quote_exclusive - purchase_exclusive) * quantity
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
