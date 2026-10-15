"""Integer unit conversions and time/expiry utilities (Section 4.3 & 5.4)."""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_UP


def rupees_to_paise(rupees: str | int | float | Decimal) -> int:
    """Convert a decimal Rupee value into exact integer paise without binary float drift."""
    dec = Decimal(str(rupees))
    paise = (dec * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(paise)


def paise_to_rupees_str(paise: int) -> str:
    """Format integer paise as Rs X,XXX.XX for UI display."""
    sign = "-" if paise < 0 else ""
    abs_p = abs(int(paise))
    rupees = abs_p // 100
    rem = abs_p % 100
    return f"{sign}Rs {rupees:,}.{rem:02d}"


def compute_absolute_expiry_min(observed_at_min: int, remaining_life_min: int) -> int:
    """Compute absolute estimated expiry once from observation time and remaining life."""
    return int(observed_at_min) + int(remaining_life_min)


def compute_remaining_life_at_min(absolute_expiry_min: int, current_time_min: int) -> int:
    """Compute remaining life at an arbitrary current time (can be <= 0 if expired)."""
    return int(absolute_expiry_min) - int(current_time_min)


def is_batch_fresh_for_sale(
    sale_at_min: int,
    freshness_buffer_min: int,
    absolute_expiry_min: int,
) -> bool:
    """Strict expiry inequality (Section 7.3): S_dc + B_dc < E_b.
    With integer minutes, S_dc + B_dc <= E_b - 1.
    """
    return (int(sale_at_min) + int(freshness_buffer_min)) <= (int(absolute_expiry_min) - 1)
