"""Rule lookup preserves source order, lazy errors and Decimal evidence."""

import json
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, Decimal, Inexact, localcontext

import pytest
from hypothesis import given, seed, settings
from hypothesis import strategies as st

from conftest import MEASURES, write_model
from pacioliscube.evaluate import CellStore, EvaluationError, consolidate_many, evaluate, explain
from pacioliscube.model import ModelError, load_model
from pacioliscube.rules import parse_rules


@pytest.mark.parametrize("areas", [
    ("['Amount']", "['Red', 'Amount']"),
    ("['Red', 'Amount']", "['Amount']"),
    ("['Red']", "['Amount']"),
    ("[]", "['Amount']"),
])
def test_overlapping_areas_keep_the_first_rule(tmp_path, areas):
    model = load_model(write_model(tmp_path, rules="\n".join(
        f"{area} = N: {index + 1};" for index, area in enumerate(areas)
    )))
    # Both areas hold Red/Amount, so the first literal, 1, wins.
    cells = [("Sales", ("Blue", "Amount")), ("Sales", ("Red", "Amount"))]
    values = list(consolidate_many(model, CellStore(), cells))
    assert values[-1].as_tuple() == Decimal("1").as_tuple()
    evidence = explain(model, CellStore(), cells)
    assert evidence["cells"]["Sales['Red', 'Amount']"]["rule"]["line"] == 1


@pytest.mark.parametrize("first", ["['Amount']", "[]"])
def test_an_earlier_match_hides_a_later_invalid_area(tmp_path, first):
    model = load_model(write_model(tmp_path, rules=f"{first} = N: 2;\n['Ghost'] = N: 3;"))
    cells = [("Sales", (colour, "Amount")) for colour in ("Red", "Blue")]
    assert list(consolidate_many(model, CellStore(), cells)) == [Decimal("2")] * 2
    assert len(explain(model, CellStore(), cells)["cells"]) == 2


@pytest.mark.parametrize("invalid, measures, message", [
    ("['Ghost']", MEASURES, "no dimension holds an element named 'Ghost'"),
    ("['Units', 'Price']", MEASURES, "area names two elements of dimension 'Measure'"),
    ("['Red']", MEASURES + ', {"Name": "Red", "Type": "Numeric"}',
     "element 'Red' is ambiguous, held by Colour, Measure"),
])
def test_an_earlier_miss_reaches_the_same_area_error(tmp_path, invalid, measures, message):
    model = load_model(write_model(
        tmp_path, rules=f"['Amount'] = N: 2;\n{invalid} = N: 3;", measures=measures,
    ))
    batch = consolidate_many(model, CellStore(), [
        ("Sales", ("Red", "Amount")), ("Sales", ("Blue", "Price")),
    ])
    assert next(batch) == Decimal("2")
    with pytest.raises(ModelError) as caught:
        next(batch)
    assert str(caught.value) == f"cube 'Sales': {message}"


@pytest.mark.parametrize("reverse", [False, True])
def test_leaf_and_consolidated_lookups_keep_separate_rule_order(tmp_path, reverse):
    model = load_model(write_model(tmp_path, rules="\n".join([
        "['Amount'] = N: 1;", "['Amount'] = C: 2;", "['Price'] = 3;", "[] = 4;",
    ])))
    cases = [(("Red", "Amount"), "1"), (("Total", "Amount"), "2"),
             (("Red", "Price"), "3"), (("Total", "Price"), "3"),
             (("Red", "Units"), "4"), (("Total", "Units"), "4")]
    if reverse:
        cases.reverse()
    cells = [("Sales", coordinate) for coordinate, _ in cases]
    assert list(consolidate_many(model, CellStore(), cells)) == [
        Decimal(value) for _, value in cases
    ]


def test_a_skipped_qualifier_does_not_resolve_its_invalid_area(tmp_path):
    model = load_model(write_model(tmp_path, rules="['Ghost'] = C: 1;\n['Amount'] = N: 2;"))
    cells = [("Sales", ("Red", "Amount")), ("Sales", ("Blue", "Amount"))]
    assert list(consolidate_many(model, CellStore(), cells)) == [Decimal("2")] * 2
    with pytest.raises(ModelError, match="Ghost"):
        list(consolidate_many(model, CellStore(), [("Sales", ("Total", "Amount"))]))


def test_evaluation_keeps_an_expression_error_before_a_later_area_error(tmp_path):
    model = load_model(write_model(
        tmp_path, rules="['Amount'] = N: 1 / 0;\n['Ghost'] = N: 2;",
    ))
    with pytest.raises(EvaluationError, match="division by zero"):
        evaluate(model, CellStore())


@pytest.mark.parametrize("declared, selector, requested", [
    ("Straße", "STRASSE", "strasse"), ("Σ", "ς", "σ"),
])
def test_rule_keys_use_python_unicode_casefold(tmp_path, declared, selector, requested):
    measures = MEASURES + ', ' + json.dumps({"Name": declared, "Type": "Numeric"})
    model = load_model(write_model(
        tmp_path, rules=f"['{selector}'] = N: 1.20;", measures=measures,
    ))
    cells = [("Sales", (colour, requested)) for colour in ("red", "blue")]
    values = list(consolidate_many(model, CellStore(), cells))
    assert [value.as_tuple() for value in values] == [Decimal("1.20").as_tuple()] * 2


@pytest.mark.parametrize("rounding, expected", [(ROUND_DOWN, "0.16"), (ROUND_HALF_EVEN, "0.17")])
def test_cached_rule_selection_keeps_decimal_context_and_trace(tmp_path, rounding, expected):
    model = load_model(write_model(tmp_path, rules="['Amount'] = N: ['Units'] / 6;"))
    store = CellStore()
    for colour in ("Red", "Blue"):
        store.set("Sales", (colour, "Units"), Decimal("1.00"))
    cells = [("Sales", (colour, "Amount")) for colour in ("Red", "Blue")]
    with localcontext() as context:
        context.prec = 2
        context.rounding = rounding
        context.clear_flags()
        evidence = explain(model, store, cells)
        assert context.flags[Inexact]
        for colour in ("Red", "Blue"):
            cell = evidence["cells"][f"Sales{[colour, 'Amount']}"]
            assert cell["value"].as_tuple() == Decimal(expected).as_tuple()
            assert cell["steps"][-1]["result"].as_tuple() == Decimal(expected).as_tuple()
        context.traps[Inexact] = True
        with pytest.raises(EvaluationError) as caught:
            list(consolidate_many(model, store, cells))
        assert type(caught.value.__cause__) is Inexact


def test_new_engines_observe_changed_rules(tmp_path):
    model = load_model(write_model(tmp_path, rules="['Amount'] = N: 1;"))
    cells = [("Sales", ("Red", "Amount"))]
    assert list(consolidate_many(model, CellStore(), cells)) == [Decimal("1")]
    cube = model.cubes["Sales"]
    model.cubes["Sales"] = cube._replace(rules=parse_rules("['Amount'] = N: 2;", cube.source))
    assert list(consolidate_many(model, CellStore(), cells)) == [Decimal("2")]


def test_a_late_entry_in_one_group_does_not_hide_an_earlier_other_group(tmp_path):
    model = load_model(write_model(tmp_path, rules="\n".join([
        "['Price'] = N: 9;", "['Red', 'Amount'] = N: 10;",
        "['Amount'] = N: 20;", "['Blue', 'Price'] = N: 30;",
    ])))
    # The initial miss prepares both shapes. Red/Amount then matches rule 2
    # before rule 3, even though rule 3 shares the first prepared shape.
    cells = [("Sales", ("Blue", "Units")), ("Sales", ("Red", "Amount")),
             ("Sales", ("Blue", "Amount"))]
    assert list(consolidate_many(model, CellStore(), cells)) == [
        Decimal("0"), Decimal("10"), Decimal("20"),
    ]


@seed(0xA2EA)
@settings(max_examples=100, database=None, deadline=None)
@given(st.lists(st.tuples(
    st.sampled_from(("", "N", "C")),
    st.sampled_from(((), ("Red",), ("Blue",), ("Total",), ("Amount",), ("Price",),
                     ("Red", "Amount"), ("Blue", "Price"), ("Total", "Amount"))),
    st.integers(min_value=1, max_value=100),
), min_size=1, max_size=30))
def test_generated_overlaps_match_first_principles(tmp_path_factory, rules):
    root = tmp_path_factory.mktemp("ordered-areas")
    text = "\n".join(
        "[" + ",".join(repr(element) for element in area) + f"] = {qualifier}: {value};"
        if qualifier else "[" + ",".join(repr(element) for element in area) + f"] = {value};"
        for qualifier, area, value in rules
    )
    model = load_model(write_model(root, rules=text))

    def expected(colour, measure):
        leaf = colour != "Total"
        for qualifier, area, literal in rules:
            if qualifier == "N" and not leaf or qualifier == "C" and leaf:
                continue
            if set(area).issubset({colour, measure}):
                return Decimal(literal)
        if not leaf:
            return expected("Red", measure) + expected("Blue", measure)
        return Decimal("0")

    cells = [("Sales", (colour, measure))
             for colour in ("Total", "Blue", "Red") for measure in ("Price", "Amount", "Units")]
    assert list(consolidate_many(model, CellStore(), cells)) == [
        expected(*coordinate) for _, coordinate in cells
    ]
