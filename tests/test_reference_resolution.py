"""Reference metadata is reused without retaining the current coordinate."""

from decimal import Decimal
from pathlib import Path
from unittest import TestCase

import pytest

from conftest import MEASURES, write_model
from pacioliscube import evaluate as backend
from pacioliscube.model import ModelError, load_model
from pacioliscube.rules import CellRef, parse_rules

check = TestCase()


@pytest.mark.parametrize("operation", ["evaluate", "batch", "explain"])
def test_public_calls_resolve_repeated_references_once(tmp_path, monkeypatch, operation):
    model = load_model(write_model(tmp_path, rules=(
        "['Amount'] = N: IF(['Units'] > 0, ['Units'] * 2 + ['Price'], ['Ghost']);"
    )))
    store = backend.CellStore()
    for index, colour in enumerate(("Red", "Blue"), start=1):
        store.set("Sales", (colour, "Units"), Decimal(index))
        store.set("Sales", (colour, "Price"), Decimal("1.25"))
    cells = [("Sales", (colour, "Amount")) for colour in ("Red", "Blue")]
    original = backend._dimension_of
    calls = []

    def counted(model, cube, element):
        calls.append(element)
        return original(model, cube, element)

    monkeypatch.setattr(backend, "_dimension_of", counted)
    for _ in range(2):
        calls.clear()
        if operation == "evaluate":
            result = backend.evaluate(model, store)
            values = [result.get(*cell) for cell in cells]
        elif operation == "batch":
            values = list(backend.consolidate_many(model, store, cells))
        else:
            result = backend.explain(model, store, cells)
            values = [result["cells"][f"{cube}{list(coordinate)}"]["value"]
                      for cube, coordinate in cells]
        # Each result is its own units times 2, plus the stored 1.25.
        expected = [Decimal(index) * 2 + Decimal("1.25") for index in (1, 2)]
        check.assertEqual([value.as_tuple() for value in values], [value.as_tuple() for value in expected])
        check.assertEqual(calls.count("Units"), 1)
        check.assertEqual(calls.count("Price"), 1)
        check.assertNotIn("Ghost", calls)


def test_ordered_selectors_and_failed_resolutions(tmp_path, monkeypatch):
    measures = MEASURES + ', {"Name": "Red", "Type": "Numeric"}' + (
        ', {"Name": "Straße", "Type": "Numeric"}'
    )
    model = load_model(write_model(tmp_path, measures=measures))
    cube = model.cubes["Sales"]
    engine = backend._Engine(model, backend.CellStore())
    original = backend._dimension_of
    calls = []

    def counted(model, cube, element):
        calls.append(element)
        return original(model, cube, element)

    monkeypatch.setattr(backend, "_dimension_of", counted)
    reference = CellRef(None, ("Units", "Price"))
    for colour in ("Blue", "Total"):
        check.assertEqual(engine.resolve_reference(reference, cube, (colour, "Amount")), (
            "Sales", (colour, "Price"),
        ))
    check.assertEqual(calls, ["Units", "Price"])
    for selectors, message in [
        (("Units", "Ghost"), "no dimension holds an element named 'Ghost'"),
        (("Red", "Ghost"), "element 'Red' is ambiguous, held by Colour, Measure"),
        (("Ghost", "Red"), "no dimension holds an element named 'Ghost'"),
    ]:
        for _ in range(2):
            calls.clear()
            with pytest.raises(ModelError) as caught:
                engine.resolve_reference(CellRef(None, selectors), cube, ("Blue", "Amount"))
            check.assertEqual(str(caught.value), f"cube 'Sales': {message}")
            check.assertEqual(calls, list(selectors[:2] if selectors[0] == "Units" else selectors[:1]))
    calls.clear()
    for colour in ("Blue", "Total"):
        check.assertEqual(engine.resolve_reference(CellRef(None, ("STRASSE",)), cube, (colour, "Amount")), (
            "Sales", (colour, "Straße"),
        ))
    check.assertEqual(calls, ["STRASSE"])


def test_new_calls_observe_changed_dimension_positions(tmp_path):
    model = load_model(write_model(tmp_path, rules="['Amount'] = N: ['Units'] * 2;"))
    store = backend.CellStore()
    store.set("Sales", ("Red", "Units"), Decimal("3.00"))
    check.assertEqual(list(backend.consolidate_many(model, store, [("Sales", ("Red", "Amount"))])), [
        Decimal("3.00") * 2,
    ])
    cube = model.cubes["Sales"]
    model.cubes["Sales"] = cube._replace(dimensions=tuple(reversed(cube.dimensions)))
    store = backend.CellStore()
    store.set("Sales", ("Units", "Red"), Decimal("5.00"))
    check.assertEqual(list(backend.consolidate_many(model, store, [("Sales", ("Amount", "Red"))])), [
        Decimal("5.00") * 2,
    ])


def test_same_reference_in_different_cubes_keeps_db_binding():
    model = load_model(Path(__file__).parent / "fixtures" / "mini")
    sales, cost = model.cubes["Sales"], model.cubes["Cost"]
    model.cubes["Sales"] = sales._replace(rules=parse_rules(
        "['Amount'] = N: ['Units'] + DB('Cost', 'Amount', !Colour);", sales.source,
    ))
    model.cubes["Cost"] = cost._replace(dimensions=("Measure", "Colour"), rules=parse_rules(
        "['Amount'] = N: ['Units'];", cost.source,
    ))
    store = backend.CellStore()
    for colour, units, cost_units in (("Red", "2", "10"), ("Blue", "4", "20")):
        store.set("Sales", (colour, "Units"), Decimal(units))
        store.set("Cost", ("Units", colour), Decimal(cost_units))
    cells = [("Sales", (colour, "Amount")) for colour in ("Red", "Blue")]
    check.assertEqual(list(backend.consolidate_many(model, store, cells)), [
        Decimal("2") + Decimal("10"), Decimal("4") + Decimal("20"),
    ])
