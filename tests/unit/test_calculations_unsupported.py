"""Every explicitly unsupported calculation kind must raise, never compute."""
import pytest

from scripts.calculations import UNSUPPORTED_KINDS, CalculationInputError, UnsupportedCalculation, unsupported

EXPECTED_KINDS = (
    "COMMISSION", "BONUS", "OVERTIME", "RENTAL", "SELF_EMPLOYED", "ASSET_DEPLETION", "GUIDELINE_DEPENDENT",
)


def test_kind_list_matches_spec():
    assert set(UNSUPPORTED_KINDS) == set(EXPECTED_KINDS)


@pytest.mark.parametrize("kind", EXPECTED_KINDS)
def test_each_kind_raises(kind):
    with pytest.raises(UnsupportedCalculation) as excinfo:
        unsupported(kind)
    assert excinfo.value.kind == kind
    assert "licensed reviewer" in str(excinfo.value)
    assert "written calculation specifications" in str(excinfo.value)


@pytest.mark.parametrize("kind", ["commission", " self-employed ", "asset depletion", "Guideline_Dependent"])
def test_kind_normalization_still_raises(kind):
    with pytest.raises(UnsupportedCalculation):
        unsupported(kind)


def test_exception_is_not_implemented_error():
    assert issubclass(UnsupportedCalculation, NotImplementedError)


@pytest.mark.parametrize("kind", ["SALARIED", "", None, 42])
def test_unknown_kind_is_input_error_not_silent(kind):
    with pytest.raises(CalculationInputError):
        unsupported(kind)


def test_unsupported_never_returns():
    for kind in EXPECTED_KINDS:
        try:
            unsupported(kind)
        except UnsupportedCalculation:
            continue
        raise AssertionError(f"{kind} did not raise")
