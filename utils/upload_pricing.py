"""Dynamic upload pricing.

Each competition has one price.
Every purchase multiplies it by `multiplier(settings)`, and between
purchases it halves every `half_life_minutes`, never below `floor_usd`.
The multiplier is chosen so that `target_per_hour` purchases an hour exactly cancel the decay.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

DEFAULT_FLOOR_USD = 5.0
DEFAULT_TARGET_PER_HOUR = 10.0
DEFAULT_HALF_LIFE_MINUTES = 30.0

MIN_TARGET_TIMES_HALF_LIFE_HOURS = 1.71

# Absorb alpha-price movement between quote and burn.
ALPHA_BUFFER = 1.1


@dataclass(frozen=True, slots=True)
class PricingSettings:
    floor_usd: float = DEFAULT_FLOOR_USD
    target_per_hour: float = DEFAULT_TARGET_PER_HOUR
    half_life_minutes: float = DEFAULT_HALF_LIFE_MINUTES


def validate_settings(settings: PricingSettings) -> None:
    """Callers already ensure every setting is positive and finite."""
    if settings.target_per_hour * settings.half_life_minutes / 60 < MIN_TARGET_TIMES_HALF_LIFE_HOURS:
        raise ValueError("target_per_hour x half_life_minutes is too small: one burn would raise the price over 50%")


def multiplier(settings: PricingSettings) -> float:
    return 2 ** (60 / (settings.target_per_hour * settings.half_life_minutes))


def current_price(*, price_usd: float, price_updated_at: datetime, settings: PricingSettings, now: datetime) -> float:
    elapsed_minutes = max(0.0, (now - price_updated_at).total_seconds() / 60)
    return max(settings.floor_usd, price_usd * 0.5 ** (elapsed_minutes / settings.half_life_minutes))


def exact_alpha_rao_for_usd(price_usd: float, alpha_price_usd: float) -> int:
    """Exact alpha (in rao) worth `price_usd` at `alpha_price_usd`. What a purchase debits."""
    return int(price_usd / alpha_price_usd * 1e9)


def alpha_rao_for_usd(price_usd: float, alpha_price_usd: float) -> int:
    """Alpha to burn for `price_usd`, with the buffer. The excess lands in the burn balance."""
    return int(price_usd / alpha_price_usd * 1e9 * ALPHA_BUFFER)
