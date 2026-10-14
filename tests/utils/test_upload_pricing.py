from datetime import datetime, timedelta, timezone

import pytest

from utils.upload_pricing import PricingSettings, alpha_rao_for_usd, current_price, multiplier, validate_settings

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
DEFAULTS = PricingSettings()


def test_price_halves_every_half_life_and_stops_at_floor():
    assert current_price(price_usd=20, price_updated_at=T0, settings=DEFAULTS, now=T0) == 20
    half_hour = T0 + timedelta(minutes=30)
    assert current_price(price_usd=20, price_updated_at=T0, settings=DEFAULTS, now=half_hour) == pytest.approx(10)
    assert current_price(price_usd=20, price_updated_at=T0, settings=DEFAULTS, now=T0 + timedelta(hours=5)) == 5


def test_clock_before_last_bump_never_raises_price():
    earlier = T0 - timedelta(minutes=5)
    assert current_price(price_usd=20, price_updated_at=T0, settings=DEFAULTS, now=earlier) == 20


def test_ten_instant_bumps_from_floor():
    price, paid = 5.0, []
    for _ in range(10):
        paid.append(price)
        price *= multiplier(DEFAULTS)
    assert paid[9] == pytest.approx(17.41, abs=0.01)
    assert price == pytest.approx(20.0)


def test_target_rate_holds_price_steady():
    six_minutes = T0 + timedelta(minutes=6)
    decayed = current_price(price_usd=8, price_updated_at=T0, settings=DEFAULTS, now=six_minutes)
    assert decayed * multiplier(DEFAULTS) == pytest.approx(8)


def test_floor_is_per_competition():
    settings = PricingSettings(floor_usd=10)
    one_hour = T0 + timedelta(hours=1)
    assert current_price(price_usd=12, price_updated_at=T0, settings=settings, now=one_hour) == 10
    assert current_price(price_usd=4, price_updated_at=T0, settings=settings, now=T0) == 10


def test_validate_rejects_multiplier_above_one_and_a_half():
    with pytest.raises(ValueError):
        validate_settings(PricingSettings(target_per_hour=2, half_life_minutes=30))
    validate_settings(PricingSettings())


def test_alpha_rao_for_usd_applies_buffer():
    assert alpha_rao_for_usd(5.0, 2.5) == int(2 * 1e9 * 1.1)
