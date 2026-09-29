"""IOM (2005) Estimated Energy Requirement: pure functions, checked against hand-computed values."""
import pytest

from health_insights import energy


def test_pal_bands_are_iom_values():
    assert energy.pal_value("male", "sedentary") == 1.0
    assert energy.pal_value("male", "low_active") == 1.11
    assert energy.pal_value("male", "active") == 1.25
    assert energy.pal_value("male", "very_active") == 1.48
    assert energy.pal_value("female", "low_active") == 1.12


def test_unknown_band_raises():
    with pytest.raises(ValueError):
        energy.pal_value("male", "couch")


def test_male_eer_hand_computed():
    # 662 - 9.53*35 + 1.11*(15.91*80 + 539.6*1.80) = 2819.4
    assert energy.eer_kcal("male", 35, 80.0, 1.80, 1.11) == pytest.approx(2819.4, abs=0.5)


def test_female_eer_hand_computed():
    # 354 - 6.91*35 + 1.12*(9.36*65 + 726*1.65) = 2135.2
    assert energy.eer_kcal("female", 35, 65.0, 1.65, 1.12) == pytest.approx(2135.2, abs=0.5)


def test_eer_rejects_nonpositive_inputs():
    with pytest.raises(ValueError):
        energy.eer_kcal("male", 35, 0.0, 1.8, 1.11)
    with pytest.raises(ValueError):
        energy.eer_kcal("male", 35, 80.0, 0.0, 1.11)
