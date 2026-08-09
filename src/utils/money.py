"""Integer-cent money conversion and presentation helpers."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def yuan_to_cents(value: int | float | str | Decimal, *, strict: bool = False) -> int:
    decimal = Decimal(str(value or 0))
    cents = decimal * 100
    rounded = cents.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if strict and cents != rounded:
        raise ValueError(f"金额 {value!r} 超过两位小数")
    return int(rounded)


def cents_to_decimal(value: int | None) -> Decimal:
    return Decimal(value or 0) / Decimal(100)


def cents_to_yuan(value: int | None) -> float:
    """Return a display-only yuan number for widgets and external formats."""
    return float(cents_to_decimal(value))


def format_yuan(value: int | None, *, decimals: int = 0) -> str:
    return f"¥{cents_to_decimal(value):,.{decimals}f}"
