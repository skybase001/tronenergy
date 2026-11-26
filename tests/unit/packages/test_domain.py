import pytest
from pydantic import ValidationError
from tronenergy_domain.config import Settings
from tronenergy_domain.models import QuoteInput, bandwidth_burn, stake_for_units


def test_bandwidth_requires_one_source_to_cover_whole_transaction():
    assert bandwidth_burn(300, 200, 200, 1000) == 300_000
    assert bandwidth_burn(300, 300, 0, 1000) == 0
    assert bandwidth_burn(300, 0, 300, 1000) == 0


def test_stake_rounding_is_integer_and_enforces_minimum():
    assert stake_for_units(1, 1, 100) == 1_000_000
    assert stake_for_units(3, 100, 7) == 42_857_143


def test_float_resource_amount_is_rejected():
    with pytest.raises(ValidationError):
        QuoteInput(resource_type="ENERGY", target_units=1.5, duration_seconds=3600)


def test_mainnet_execution_requires_later_acceptance():
    with pytest.raises(ValidationError):
        Settings(network="mainnet", broadcasting_enabled=True, _env_file=None)
