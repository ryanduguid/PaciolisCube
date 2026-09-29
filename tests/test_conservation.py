"""Consolidation conservation in the shipped model, checked from leaf cells.

Each test adds up leaf PnL cells in the test body and compares the total with
the figure the engine consolidates for the parent element: Group against its
two entities and six cost centres, each quarter and FY against their months,
and EBIT against the signed sum of its leaf accounts. The element lists are
written out here rather than read from the dimension files, so a dropped
child, a wrong weight or a double count in the model turns a test red.
"""

from decimal import Decimal
from itertools import product

from pacioliscube.evaluate import consolidate_many
from test_calculations import ACTUAL, BUDGET, CALCULATED, MODEL, pnl

ZERO = Decimal("0")
TOLERANCE = Decimal("0.000000001")
VERSIONS = (BUDGET, ACTUAL)
ENTITIES = ("CivilCo", "HaulCo")
COST_CENTRES = ("Earthworks", "Drill and Blast", "Haulage", "Workshop", "Site Admin", "Corporate")
MONTHS = ("Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun")
QUARTERS = {
    "Q1": ("Jul", "Aug", "Sep"),
    "Q2": ("Oct", "Nov", "Dec"),
    "Q3": ("Jan", "Feb", "Mar"),
    "Q4": ("Apr", "May", "Jun"),
}
REVENUE_ACCOUNTS = ("Contract Revenue", "Plant Hire Revenue")
COST_ACCOUNTS = (
    "Subcontractor Costs", "Fuel", "Consumables",
    "Wages and Salaries", "Superannuation", "Payroll Tax",
    "Repairs and Maintenance", "Insurance", "Site Overheads", "Corporate Overheads",
    "Depreciation",
)
LEAF_ACCOUNTS = REVENUE_ACCOUNTS + COST_ACCOUNTS


def nodes(coordinates):
    """PnL Amount figures read through coordinates that may name consolidations."""
    return list(consolidate_many(MODEL, CALCULATED, [("PnL", c + ("Amount",)) for c in coordinates]))


def same(total, expected):
    return abs(total - expected) <= TOLERANCE


def leaf_sum(version, months, entities, cost_centres, accounts):
    return sum(
        (pnl(version, m, e, cc, a) for m, e, cc, a in product(months, entities, cost_centres, accounts)),
        ZERO,
    )


def test_the_model_has_enough_calculated_leaves_for_these_checks_to_mean_something():
    # A floor, so an empty store cannot pass every equality below as 0 == 0.
    non_zero = [
        cell
        for version in VERSIONS
        for cell in product(MONTHS, ENTITIES, COST_CENTRES, LEAF_ACCOUNTS)
        if pnl(version, *cell) != ZERO
    ]
    assert len(non_zero) > 500
    # Every month, entity, cost centre and account carries a non-zero leaf, so
    # dropping any one child from a consolidation changes a total checked below.
    for position, members in enumerate((MONTHS, ENTITIES, COST_CENTRES, LEAF_ACCOUNTS)):
        for member in members:
            assert any(cell[position] == member for cell in non_zero), member
    # Every leaf account also moves the year's total by more than the
    # tolerance same() allows, so an account dropped from EBIT cannot leave the
    # EBIT identity inside that tolerance by netting to nil or near it.
    for version in VERSIONS:
        for account in LEAF_ACCOUNTS:
            total = leaf_sum(version, MONTHS, ENTITIES, COST_CENTRES, (account,))
            assert abs(total) > TOLERANCE, (version, account)
    assert leaf_sum(BUDGET, MONTHS, ENTITIES, COST_CENTRES, REVENUE_ACCOUNTS) > ZERO
    assert leaf_sum(ACTUAL, MONTHS, ENTITIES, COST_CENTRES, REVENUE_ACCOUNTS) > ZERO


def test_group_is_the_sum_of_every_entity_and_cost_centre_leaf():
    for version in VERSIONS:
        cells = list(product(MONTHS, LEAF_ACCOUNTS))
        totals = nodes([version + (month, "Group", "All Cost Centres", account) for month, account in cells])
        for (month, account), total in zip(cells, totals, strict=True):
            expected = leaf_sum(version, (month,), ENTITIES, COST_CENTRES, (account,))
            assert same(total, expected), (version, month, account)


def test_each_entity_total_is_the_sum_of_its_cost_centres():
    for version in VERSIONS:
        cells = list(product(MONTHS, ENTITIES, LEAF_ACCOUNTS))
        totals = nodes([version + (month, entity, "All Cost Centres", account) for month, entity, account in cells])
        for (month, entity, account), total in zip(cells, totals, strict=True):
            expected = leaf_sum(version, (month,), (entity,), COST_CENTRES, (account,))
            assert same(total, expected), (version, month, entity, account)


def test_quarters_and_the_year_are_the_sum_of_their_months():
    periods = (*QUARTERS, "FY")
    for version in VERSIONS:
        leaves = list(product(ENTITIES, COST_CENTRES, LEAF_ACCOUNTS))
        values = iter(nodes([version + (period, e, cc, a) for e, cc, a in leaves for period in periods]))
        for entity, cost_centre, account in leaves:
            by_month = {m: pnl(version, m, entity, cost_centre, account) for m in MONTHS}
            for quarter, members in QUARTERS.items():
                assert same(next(values), sum((by_month[m] for m in members), ZERO)), (
                    version, quarter, entity, cost_centre, account,
                )
            assert same(next(values), sum(by_month.values(), ZERO)), (
                version, "FY", entity, cost_centre, account,
            )


def test_ebit_is_revenue_leaves_less_every_cost_leaf():
    # Written from the statement's meaning, not from the Account weights: EBIT
    # is revenue less direct costs, employment costs, overheads and depreciation.
    for version in VERSIONS:
        for entity in (*ENTITIES, "Group"):
            members = ENTITIES if entity == "Group" else (entity,)
            [ebit] = nodes([version + ("FY", entity, "All Cost Centres", "EBIT")])
            revenue = leaf_sum(version, MONTHS, members, COST_CENTRES, REVENUE_ACCOUNTS)
            costs = leaf_sum(version, MONTHS, members, COST_CENTRES, COST_ACCOUNTS)
            assert same(ebit, revenue - costs), (version, entity)
