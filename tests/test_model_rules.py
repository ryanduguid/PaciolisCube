"""The shipped rules: discipline checks a Planning Analytics reviewer runs by eye."""

from pathlib import Path

from pacioliscube.model import load_model
from pacioliscube.validate import validate_model

MODEL_ROOT = Path(__file__).resolve().parents[1] / "model"

CALCULATED_CUBES = ("Workforce", "Revenue", "Capex", "PnL")


def rules_text(name: str) -> str:
    return (MODEL_ROOT / "cubes" / f"{name}.rules").read_text(encoding="utf-8")


def test_every_calculated_cube_uses_skipcheck():
    model = load_model(MODEL_ROOT)
    for name in CALCULATED_CUBES:
        ruleset = model.cubes[name].rules
        assert ruleset is not None, name
        assert ruleset.skipcheck is True, name


def test_every_source_cube_declares_feeders():
    model = load_model(MODEL_ROOT)
    for name in ("Workforce", "Revenue", "Capex"):
        assert model.cubes[name].rules.feeders, name


def test_the_reporting_cube_is_fed_from_its_source_cubes():
    model = load_model(MODEL_ROOT)
    cross = [
        feeder
        for name in ("Workforce", "Revenue", "Capex")
        for feeder in model.cubes[name].rules.feeders
        if feeder.target_cube == "PnL"
    ]
    assert len(cross) >= 6


def test_no_statutory_rate_is_hard_coded_in_rule_text():
    for name in CALCULATED_CUBES:
        text = rules_text(name)
        for forbidden in ("0.12", "12%", "0.0545", "5.45", "270830", "270,830", "1200000", "1,200,000"):
            assert forbidden not in text, f"{name} hard codes {forbidden}"


def test_superannuation_reads_its_rate_from_the_drivers_cube():
    text = rules_text("Workforce")
    assert "DB('Drivers'" in text
    assert "'SG Rate'" in text
    assert "'Maximum Contribution Base'" in text


def test_payroll_tax_is_levied_on_wages_grossed_up_by_superannuation():
    text = rules_text("Workforce")
    assert "( ['Base Pay'] + ['Superannuation Cost'] )" in text


def test_the_threshold_credit_sits_with_the_designated_group_employer():
    text = rules_text("PnL")
    assert "'Payroll Tax Threshold'" in text
    assert text.index("'CivilCo', 'Corporate', 'Payroll Tax'") < text.index(
        "['Payroll Tax', 'Amount']"
    ), "the specific statement must come before the general one, first match wins"


def test_the_drivers_cube_is_pure_input():
    assert load_model(MODEL_ROOT).cubes["Drivers"].rules is None


def test_the_model_validates_with_no_errors_and_no_warnings():
    findings = validate_model(load_model(MODEL_ROOT))
    assert findings == (), [f"{f.code}: {f.message} ({f.location})" for f in findings]
