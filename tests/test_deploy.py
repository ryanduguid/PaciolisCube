"""Planning a deployment offline, and translating a model into TM1py objects.

The whole file runs without a Planning Analytics server. Everything above the
``tm1py`` fixture also runs with TM1py uninstalled, which is the state the sdist
run inside the ci.yml package job is in. The test job installs the dev extra,
which carries TM1py, so those runs cover the rest of the file too.
"""

import json
import sys
from pathlib import Path

import pytest

from pacioliscube.deploy import (
    CREATED,
    CUBE,
    DIMENSION,
    PROCESS,
    UNCHANGED,
    UPDATED,
    DeploymentError,
    Operation,
    deploy,
    plan,
    split_procedures,
    to_tm1py_cube,
    to_tm1py_dimension,
    to_tm1py_process,
)
from pacioliscube.model import ModelError, load_dimension, load_model, load_process

REPO = Path(__file__).resolve().parents[1]
MINI = Path(__file__).parent / "fixtures" / "mini"


@pytest.fixture
def without_tm1py(monkeypatch):
    """Make ``import TM1py`` fail, whether or not the library is installed."""
    monkeypatch.setitem(sys.modules, "TM1py", None)
    monkeypatch.setitem(sys.modules, "TM1py.Objects", None)


# Every method the deployer may reach for on a service, split by whether it
# changes the database. A method that reads was recording nothing before, so
# an empty call log proved only that nothing had been written, and a check that
# had stopped running looked the same as a check that had passed.
READ_METHODS = frozenset(
    {
        "dimensions.exists",
        "hierarchies.exists",
        "cubes.exists",
        "cubes.get_dimension_names",
        "processes.exists",
    }
)

WRITE_METHODS = frozenset(
    {
        "dimensions.create",
        "dimensions.update",
        "hierarchies.create",
        "hierarchies.update",
        "cubes.create",
        "cubes.update",
        "cubes.update_or_create_rules",
        "processes.create",
        "processes.update",
    }
)


class FakeDimensions:
    def __init__(self, service):
        self.service = service

    def exists(self, name):
        self.service.record("dimensions.exists", name)
        return name in self.service.dimension_names

    def create(self, dimension):
        self.service.record("dimensions.create", dimension.name)

    def update(self, dimension, **kwargs):
        self.service.record("dimensions.update", dimension.name)

    def delete(self, name):
        raise AssertionError(f"the deployer deleted dimension {name!r}")


class FakeHierarchies:
    def __init__(self, service):
        self.service = service

    def exists(self, dimension_name, hierarchy_name):
        self.service.record("hierarchies.exists", hierarchy_name)
        return (dimension_name, hierarchy_name) in self.service.hierarchy_names

    def create(self, hierarchy):
        self.service.record("hierarchies.create", hierarchy.name)

    def update(self, hierarchy, keep_existing_attributes=False, **kwargs):
        self.service.record("hierarchies.update", hierarchy.name)
        self.service.kept_attributes.append(keep_existing_attributes)

    def delete(self, dimension_name, hierarchy_name):
        raise AssertionError(f"the deployer deleted hierarchy {hierarchy_name!r}")


class FakeCubes:
    def __init__(self, service):
        self.service = service

    def exists(self, cube_name):
        self.service.record("cubes.exists", cube_name)
        return cube_name in self.service.cube_dimensions

    def get_dimension_names(self, cube_name):
        self.service.record("cubes.get_dimension_names", cube_name)
        return list(self.service.cube_dimensions[cube_name])

    def create(self, cube):
        self.service.record("cubes.create", cube.name)

    def update(self, cube):
        self.service.record("cubes.update", cube.name)

    def update_or_create_rules(self, cube_name, rules):
        self.service.record("cubes.update_or_create_rules", cube_name)
        self.service.written_rules[cube_name] = rules.text

    def delete(self, cube_name):
        raise AssertionError(f"the deployer deleted cube {cube_name!r}")


class FakeProcesses:
    def __init__(self, service):
        self.service = service

    def exists(self, name):
        self.service.record("processes.exists", name)
        return name in self.service.process_names

    def create(self, process):
        self.service.record("processes.create", process.name)

    def update(self, process):
        self.service.record("processes.update", process.name)

    def delete(self, name):
        raise AssertionError(f"the deployer deleted process {name!r}")


class FakeService:
    """A stand in for TM1Service that records calls rather than making them.

    ``calls`` holds every method the deployer reached for, reads included, so an
    empty ``calls`` means the database was never approached at all. ``writes``
    is the subset that would have changed something, so an empty ``writes``
    alongside a non empty ``calls`` means the deployer looked and then refused.
    Every delete method raises, so a deployment that touched one would fail the
    test that ran it rather than pass quietly.
    """

    def __init__(self, dimensions=(), hierarchies=(), cubes=None, processes=()):
        self.dimension_names = set(dimensions)
        self.hierarchy_names = set(hierarchies)
        self.cube_dimensions = dict(cubes or {})
        self.process_names = set(processes)
        self.calls = []
        self.kept_attributes = []
        self.written_rules = {}
        self.dimensions = FakeDimensions(self)
        self.hierarchies = FakeHierarchies(self)
        self.cubes = FakeCubes(self)
        self.processes = FakeProcesses(self)

    def record(self, method, name):
        # An unclassified method would land in calls but never in writes, which
        # would quietly weaken every zero write assertion in this file.
        assert method in READ_METHODS or method in WRITE_METHODS, method
        self.calls.append((method, name))

    @property
    def writes(self):
        return [call for call in self.calls if call[0] in WRITE_METHODS]


def kinds(operations):
    return [operation.kind for operation in operations]


def named(operations, kind, name):
    (found,) = [
        operation
        for operation in operations
        if operation.kind == kind and operation.name == name
    ]
    return found


# Planning, which needs neither a server nor TM1py.


def test_a_plan_is_plain_data_with_no_tm1py_import(without_tm1py):
    operations = plan(load_model(MINI))
    assert all(isinstance(operation, Operation) for operation in operations)


def test_a_plan_puts_every_dimension_before_every_cube_and_process():
    order = kinds(plan(load_model(MINI)))
    assert order == [DIMENSION, DIMENSION, CUBE, CUBE, PROCESS]


def test_a_plan_covers_every_object_the_model_holds():
    model = load_model(REPO / "model")
    operations = plan(model)
    assert len(operations) == len(model.dimensions) + len(model.cubes) + len(model.processes)


def test_a_plan_keeps_the_manifest_order_within_a_kind():
    model = load_model(REPO / "model")
    planned = [operation.name for operation in plan(model) if operation.kind == DIMENSION]
    assert planned == list(model.dimensions)


def test_a_planned_operation_names_the_file_it_comes_from():
    operation = named(plan(load_model(MINI)), CUBE, "Sales")
    assert operation.source.replace("\\", "/").endswith("cubes/Sales.json")


def test_a_planned_cube_depends_on_its_dimensions_in_order():
    operation = named(plan(load_model(MINI)), CUBE, "Sales")
    assert operation.depends_on == ("Colour", "Measure")


def test_a_planned_dimension_depends_on_nothing():
    operation = named(plan(load_model(MINI)), DIMENSION, "Colour")
    assert operation.depends_on == ()


def test_a_planned_process_lists_the_cubes_its_script_quotes():
    operation = named(plan(load_model(REPO / "model")), PROCESS, "LoadDrivers")
    assert operation.depends_on == ("Drivers",)


def test_a_planned_dimension_counts_its_hierarchies_elements_and_edges():
    operation = named(plan(load_model(MINI)), DIMENSION, "Colour")
    assert operation.summary == "1 hierarchy, 4 elements, 3 edges"


def test_a_planned_cube_counts_its_rules_and_feeders():
    operation = named(plan(load_model(MINI)), CUBE, "Sales")
    assert operation.summary == "2 dimensions, 2 rules, 2 feeders"


def test_a_planned_cube_without_rules_says_so():
    operation = named(plan(load_model(MINI)), CUBE, "Cost")
    assert operation.summary == "2 dimensions, no rules"


def test_a_planned_process_names_its_datasource_and_the_procedures_that_carry_code():
    operation = named(plan(load_model(MINI)), PROCESS, "Load")
    assert operation.summary == "1 parameter, ASCII datasource, code in prolog"


# Splitting a git native .ti file into TurboIntegrator procedures.


def test_each_region_becomes_the_procedure_it_names():
    script = (
        "#Region Prolog\na = 1;\n#EndRegion\n"
        "#Region Metadata\nb = 2;\n#EndRegion\n"
        "#Region Data\nc = 3;\n#EndRegion\n"
        "#Region Epilog\nd = 4;\n#EndRegion\n"
    )
    assert split_procedures(script) == ("a = 1;", "b = 2;", "c = 3;", "d = 4;")


def test_a_region_marker_is_not_carried_into_the_procedure():
    assert "Region" not in split_procedures("#Region Data\nc = 3;\n#EndRegion").data


def test_a_region_name_is_matched_without_regard_to_case():
    assert split_procedures("#REGION epilog\nd = 4;\n#endregion").epilog == "d = 4;"


def test_a_script_with_no_regions_becomes_the_prolog():
    assert split_procedures("a = 1;\nb = 2;").prolog == "a = 1;\nb = 2;"


def test_text_outside_every_region_joins_the_prolog():
    script = "a = 1;\n#Region Data\nc = 3;\n#EndRegion\n"
    procedures = split_procedures(script)
    assert procedures.prolog == "a = 1;"
    assert procedures.data == "c = 3;"


def test_a_region_name_outside_the_four_is_refused_by_name():
    with pytest.raises(ModelError) as caught:
        split_procedures("#Region Prologue\na = 1;\n#EndRegion", "Load.ti")
    assert "Prologue" in str(caught.value)
    assert "Load.ti line 1" in str(caught.value)


def test_a_region_opened_inside_a_region_is_refused():
    with pytest.raises(ModelError) as caught:
        split_procedures("#Region Prolog\n#Region Data\n#EndRegion\n#EndRegion", "Load.ti")
    message = str(caught.value)
    assert "opens inside" in message
    # Both labels keep the spelling the file used, and both lines are named, so
    # the message points at the two places a reader has to look.
    assert "'Data'" in message
    assert "'Prolog'" in message
    assert "line 2" in message
    assert "from line 1" in message
    assert "Load.ti" in message


def test_an_endregion_with_no_open_region_is_refused():
    with pytest.raises(ModelError) as caught:
        split_procedures("a = 1;\n#EndRegion")
    assert "#EndRegion without a #Region" in str(caught.value)


def test_a_region_that_is_never_closed_is_refused_by_name_line_and_file():
    with pytest.raises(ModelError) as caught:
        split_procedures("a = 1;\n#Region Data\nc = 3;", "Load.ti")
    assert "never closed" in str(caught.value)
    # The line of the #Region that was left open, not the line the file ends on,
    # because that is the line the modeller has to go and edit.
    assert "Load.ti line 2" in str(caught.value)
    assert "'Data'" in str(caught.value)


def test_an_unclosed_region_is_named_the_way_the_source_spelt_it():
    with pytest.raises(ModelError) as caught:
        split_procedures("#REGION Epilog\nd = 4;", "Load.ti")
    assert "'Epilog'" in str(caught.value)


def test_a_comment_that_merely_starts_with_region_is_left_as_code():
    assert split_procedures("#Regional totals follow\na = 1;").prolog.startswith("#Regional")


# Refusing to translate when TM1py is not installed.


def test_translating_a_dimension_without_tm1py_names_the_extra(without_tm1py):
    with pytest.raises(ImportError) as caught:
        to_tm1py_dimension(load_model(MINI).dimensions["Colour"])
    assert "pacioliscube[deploy]" in str(caught.value)


def test_translating_a_cube_without_tm1py_names_the_extra(without_tm1py):
    with pytest.raises(ImportError) as caught:
        to_tm1py_cube(load_model(MINI).cubes["Sales"])
    assert "pacioliscube[deploy]" in str(caught.value)


def test_translating_a_process_without_tm1py_names_the_extra(without_tm1py):
    with pytest.raises(ImportError) as caught:
        to_tm1py_process(load_model(MINI).processes["Load"])
    assert "pacioliscube[deploy]" in str(caught.value)


def test_the_missing_tm1py_message_says_the_rest_of_the_package_is_offline(without_tm1py):
    with pytest.raises(ImportError) as caught:
        to_tm1py_dimension(load_model(MINI).dimensions["Colour"])
    assert "offline" in str(caught.value)


def test_deploying_without_tm1py_never_touches_the_service(without_tm1py):
    service = FakeService()
    with pytest.raises(ImportError):
        deploy(load_model(MINI), service)
    assert service.calls == []


def test_deploying_a_model_with_no_objects_without_tm1py_is_still_refused(
    without_tm1py, tmp_path
):
    # Nothing to translate means nothing else raises, so this is the one case
    # that pins the import check deploy does before it starts work. Without it
    # a model with no objects would report a successful deployment on a machine
    # that cannot deploy at all.
    (tmp_path / "tm1project.json").write_text(
        '{"Name": "Empty", "Objects": {}}', encoding="utf-8"
    )
    service = FakeService()
    with pytest.raises(ImportError) as caught:
        deploy(load_model(tmp_path), service)
    assert "pacioliscube[deploy]" in str(caught.value)
    assert service.calls == []


def test_deploy_cannot_be_called_without_naming_a_service():
    with pytest.raises(TypeError):
        deploy(load_model(MINI))


# Everything below needs TM1py. The test job in ci.yml installs it through the
# dev extra, so those runs exercise all of it. The sdist run inside the package
# job installs pytest alone, and that is where the skip below takes effect.


@pytest.fixture(scope="module")
def tm1py():
    """The TM1py package, or a skip for the test asking for it.

    A skip per test rather than per file, so the offline half of this suite
    still runs on a machine that has never heard of Planning Analytics.
    """
    return pytest.importorskip("TM1py", reason="TM1py is an optional extra")


def fractional_dimension(root: Path):
    """A one edge dimension whose weight is not a whole number."""
    (root / "Split.hierarchies").mkdir()
    (root / "Split.hierarchies" / "Split.json").write_text(
        '{"Name": "Split",'
        ' "Elements": [{"Name": "All", "Type": "Consolidated"},'
        ' {"Name": "Half", "Type": "Numeric"}],'
        ' "Edges": [{"ParentName": "All", "ComponentName": "Half", "Weight": 0.5}]}',
        encoding="utf-8",
    )
    path = root / "Split.json"
    path.write_text(
        '{"Name": "Split", "Hierarchies@Code.links": ["Split.hierarchies/Split.json"]}',
        encoding="utf-8",
    )
    return load_dimension(path, root)


@pytest.fixture
def four_region_process(tmp_path):
    """A process whose four procedures each carry a different line of code.

    The MINI fixture's Load.ti opens a Prolog and nothing else, so its metadata,
    data and epilog all come out empty and a translation that put one procedure
    where another belongs would look identical. Every procedure here is
    distinguishable, so a swap shows up. On a server the distinction is real: a
    data loop running in the metadata pass is a different process.
    """
    (tmp_path / "Split.ti").write_text(
        "#Region Prolog\nsProlog = 1;\n#EndRegion\n"
        "#Region Metadata\nsMetadata = 2;\n#EndRegion\n"
        "#Region Data\nsData = 3;\n#EndRegion\n"
        "#Region Epilog\nsEpilog = 4;\n#EndRegion\n",
        encoding="utf-8",
    )
    path = tmp_path / "Split.json"
    path.write_text(
        '{"Name": "Split", "Code@Code.link": "Split.ti", "DataSource": {"Type": "None"}}',
        encoding="utf-8",
    )
    return load_process(path, tmp_path)


def test_a_translated_dimension_carries_its_name_and_hierarchies(tm1py):
    translated = to_tm1py_dimension(load_model(MINI).dimensions["Colour"])
    assert isinstance(translated, tm1py.Dimension)
    assert translated.name == "Colour"
    assert translated.hierarchy_names == ["Colour"]


def test_a_translated_element_keeps_its_type(tm1py):
    hierarchy = to_tm1py_dimension(load_model(MINI).dimensions["Colour"]).hierarchies[0]
    assert str(hierarchy.elements["Total"].element_type) == "Consolidated"
    assert str(hierarchy.elements["Red"].element_type) == "Numeric"


def test_a_translated_edge_keeps_its_parent_component_and_weight(tm1py):
    hierarchy = to_tm1py_dimension(load_model(MINI).dimensions["Colour"]).hierarchies[0]
    assert hierarchy.edges[("Total", "Contra")] == -1


def test_a_whole_number_weight_crosses_as_an_integer(tm1py):
    hierarchy = to_tm1py_dimension(load_model(MINI).dimensions["Colour"]).hierarchies[0]
    assert isinstance(hierarchy.edges[("Total", "Red")], int)


def test_a_fractional_weight_crosses_as_a_float(tm1py, tmp_path):
    hierarchy = to_tm1py_dimension(fractional_dimension(tmp_path)).hierarchies[0]
    assert hierarchy.edges[("All", "Half")] == pytest.approx(0.5)


def test_a_translated_dimension_body_is_json_a_server_could_take(tm1py, tmp_path):
    # A Decimal weight would raise here rather than reach the wire, which is the
    # reason weights are converted at all.
    body = json.loads(to_tm1py_dimension(fractional_dimension(tmp_path)).body)
    assert body["Hierarchies"][0]["Edges"][0]["Weight"] == pytest.approx(0.5)


def test_a_translated_cube_keeps_its_dimension_order(tm1py):
    assert to_tm1py_cube(load_model(MINI).cubes["Sales"]).dimensions == ["Colour", "Measure"]


def test_a_translated_cube_carries_its_rule_file_text_unchanged(tm1py):
    cube = load_model(MINI).cubes["Sales"]
    assert to_tm1py_cube(cube).rules.text == cube.rules_source.read_text(encoding="utf-8-sig")


def test_a_translated_cube_with_no_rules_link_carries_no_rules(tm1py):
    assert to_tm1py_cube(load_model(MINI).cubes["Cost"]).rules is None


def test_a_cube_whose_rules_file_has_gone_names_the_file(tm1py, tmp_path):
    cube = load_model(MINI).cubes["Sales"]._replace(rules_source=tmp_path / "Gone.rules")
    with pytest.raises(ModelError) as caught:
        to_tm1py_cube(cube)
    assert "Gone.rules" in str(caught.value)


def test_a_translated_process_puts_each_region_in_its_own_procedure(tm1py):
    translated = to_tm1py_process(load_model(MINI).processes["Load"])
    assert "sVersion = pVersion;" in translated.prolog_procedure
    assert "sVersion" not in translated.data_procedure


def test_a_translated_process_puts_every_region_in_the_procedure_it_names(
    tm1py, four_region_process
):
    translated = to_tm1py_process(four_region_process)
    assert "sProlog = 1;" in translated.prolog_procedure
    assert "sMetadata = 2;" in translated.metadata_procedure
    assert "sData = 3;" in translated.data_procedure
    assert "sEpilog = 4;" in translated.epilog_procedure


def test_a_translated_process_puts_no_region_in_a_procedure_it_does_not_name(
    tm1py, four_region_process
):
    translated = to_tm1py_process(four_region_process)
    assert "sMetadata" not in translated.data_procedure
    assert "sData" not in translated.metadata_procedure
    assert "sEpilog" not in translated.prolog_procedure
    assert "sProlog" not in translated.epilog_procedure


def test_a_translated_process_keeps_its_parameters(tm1py):
    translated = to_tm1py_process(load_model(MINI).processes["Load"])
    assert translated.parameters[0]["Name"] == "pVersion"


def test_a_translated_process_recovers_the_variables_the_loader_drops(tm1py):
    translated = to_tm1py_process(load_model(REPO / "model").processes["LoadDrivers"])
    assert [variable["Name"] for variable in translated.variables] == [
        "vYear",
        "vVersion",
        "vPeriod",
        "vMeasure",
        "vValue",
    ]


def test_a_translated_process_carries_its_datasource_settings(tm1py):
    body = to_tm1py_process(load_model(REPO / "model").processes["LoadDrivers"]).body_as_dict
    assert body["DataSource"]["Type"] == "ASCII"
    assert body["DataSource"]["asciiDelimiterChar"] == ","
    assert body["DataSource"]["dataSourceNameForServer"] == "drivers.csv"


def test_a_datasource_password_in_model_source_is_refused(tm1py):
    process = load_model(MINI).processes["Load"]
    with_password = process._replace(datasource={"Type": "ODBC", "password": "sw0rdf1sh"})
    with pytest.raises(ModelError) as caught:
        to_tm1py_process(with_password)
    message = str(caught.value)
    # The dedicated refusal, not the unmapped key branch. That branch quotes the
    # key back, so its message carries the word password too and asserting on
    # that word alone would not notice the dedicated guard going missing.
    assert "Model source is committed to git" in message
    assert "set it on the server" in message
    assert "Known keys are" not in message


def test_refusing_a_datasource_password_does_not_repeat_the_password(tm1py):
    process = load_model(MINI).processes["Load"]
    with_password = process._replace(datasource={"Type": "ODBC", "password": "sw0rdf1sh"})
    with pytest.raises(ModelError) as caught:
        to_tm1py_process(with_password)
    assert "sw0rdf1sh" not in str(caught.value)


def test_a_datasource_key_the_deployer_cannot_map_is_refused_by_name(tm1py):
    process = load_model(MINI).processes["Load"]
    with pytest.raises(ModelError) as caught:
        to_tm1py_process(process._replace(datasource={"Type": "ASCII", "asciiMystery": 1}))
    assert "asciiMystery" in str(caught.value)


def test_every_object_of_the_repository_model_translates(tm1py):
    model = load_model(REPO / "model")
    for dimension in model.dimensions.values():
        assert to_tm1py_dimension(dimension).name == dimension.name
    for cube in model.cubes.values():
        assert to_tm1py_cube(cube).name == cube.name
    for process in model.processes.values():
        assert to_tm1py_process(process).name == process.name


# Deploying against a recording stand in for TM1Service.


def test_a_deployment_writes_dimensions_then_cubes_then_processes(tm1py):
    service = FakeService()
    deploy(load_model(MINI), service)
    assert [method for method, _ in service.writes] == [
        "dimensions.create",
        "dimensions.create",
        "cubes.create",
        "cubes.create",
        "processes.create",
    ]


def test_a_deployment_reports_what_it_did_for_every_object(tm1py):
    changes = deploy(load_model(MINI), FakeService())
    assert [(change.kind, change.name, change.action) for change in changes] == [
        (DIMENSION, "Colour", CREATED),
        (DIMENSION, "Measure", CREATED),
        (CUBE, "Sales", CREATED),
        (CUBE, "Cost", CREATED),
        (PROCESS, "Load", CREATED),
    ]


def test_an_existing_dimension_is_updated_one_hierarchy_at_a_time(tm1py):
    service = FakeService(dimensions=["Colour"], hierarchies=[("Colour", "Colour")])
    changes = deploy(load_model(MINI), service)
    assert ("hierarchies.update", "Colour") in service.calls
    assert ("dimensions.update", "Colour") not in service.calls
    assert changes[0].action == UPDATED


def test_updating_a_hierarchy_keeps_the_element_attributes_the_server_holds(tm1py):
    service = FakeService(dimensions=["Colour"], hierarchies=[("Colour", "Colour")])
    deploy(load_model(MINI), service)
    assert service.kept_attributes == [True]


def test_a_hierarchy_the_server_lacks_is_created_under_an_existing_dimension(tm1py):
    service = FakeService(dimensions=["Colour"])
    deploy(load_model(MINI), service)
    assert ("hierarchies.create", "Colour") in service.calls


def test_an_existing_cube_has_only_its_rules_written(tm1py):
    service = FakeService(cubes={"Sales": ["Colour", "Measure"]})
    deploy(load_model(MINI), service)
    assert ("cubes.update_or_create_rules", "Sales") in service.calls
    assert ("cubes.update", "Sales") not in service.calls
    assert "SKIPCHECK;" in service.written_rules["Sales"]


def test_an_existing_cube_the_model_gives_no_rules_is_left_alone(tm1py):
    service = FakeService(cubes={"Cost": ["Colour", "Measure"]})
    changes = deploy(load_model(MINI), service)
    assert [change for change in changes if change.name == "Cost"][0].action == UNCHANGED
    assert "Cost" not in service.written_rules


def test_an_existing_process_is_updated_rather_than_created(tm1py):
    service = FakeService(processes=["Load"])
    deploy(load_model(MINI), service)
    assert ("processes.update", "Load") in service.calls
    assert ("processes.create", "Load") not in service.calls


def test_a_cube_whose_server_dimensions_differ_is_refused_before_anything_is_written(tm1py):
    service = FakeService(cubes={"Sales": ["Colour"]})
    with pytest.raises(DeploymentError) as caught:
        deploy(load_model(MINI), service)
    message = str(caught.value)
    assert "'Sales'" in message
    assert "Colour and Measure" in message
    # The model's list is a superset of this fixture's server list, so asserting
    # only the model side still passes on a message that never says what the
    # server actually holds. Both halves have to be named.
    assert "exists on the server over Colour," in message
    assert service.writes == []
    # This refusal has to read the server to reach its verdict, so a run that
    # made no call at all would mean the check had stopped running.
    assert ("cubes.exists", "Sales") in service.calls


def test_a_cube_dimension_order_is_compared_ignoring_case_and_spaces(tm1py):
    service = FakeService(cubes={"Sales": ["col our", "MEASURE"]})
    deploy(load_model(MINI), service)
    assert ("cubes.update_or_create_rules", "Sales") in service.calls


def test_a_cube_naming_a_dimension_the_model_lacks_is_refused(tm1py):
    model = load_model(MINI)
    model.cubes["Sales"] = model.cubes["Sales"]._replace(dimensions=("Colour", "Ghost"))
    service = FakeService()
    with pytest.raises(DeploymentError) as caught:
        deploy(model, service)
    assert "Ghost" in str(caught.value)
    # The model contradicts itself, which takes no server to see, so the
    # database is never approached at all.
    assert service.calls == []


def test_only_a_cube_the_model_gives_no_rules_is_ever_reported_unchanged(tm1py):
    # The deployer reads no dimension or process back off the server to compare,
    # so it has no grounds to call one unchanged. Everything it writes to is
    # reported updated on the strength of the write alone, and the docstring on
    # deploy says as much.
    model = load_model(MINI)
    service = FakeService(
        dimensions=list(model.dimensions),
        hierarchies=[(name, name) for name in model.dimensions],
        cubes={cube.name: list(cube.dimensions) for cube in model.cubes.values()},
        processes=list(model.processes),
    )
    changes = deploy(model, service)
    assert [
        (change.kind, change.name) for change in changes if change.action == UNCHANGED
    ] == [(CUBE, "Cost")]
    assert ("hierarchies.update", "Colour") in service.writes
    assert ("processes.update", "Load") in service.writes


def test_a_deployment_of_the_repository_model_deletes_nothing(tm1py):
    model = load_model(REPO / "model")
    service = FakeService(
        dimensions=list(model.dimensions),
        hierarchies=[(name, name) for name in model.dimensions],
        cubes={cube.name: list(cube.dimensions) for cube in model.cubes.values()},
        processes=list(model.processes),
    )
    # Every delete method on the stand in raises, so reaching the end proves the
    # deployer called none of them.
    assert len(deploy(model, service)) == len(plan(model))


# A refusal that needs no server, arriving after the first object is already on
# the database, is the half applied deployment the module says it will not do.
# Each of these four is a translation failure, and each one used to fire from
# inside the write loop.


def test_a_process_region_name_the_deployer_cannot_place_stops_every_write(tm1py):
    model = load_model(MINI)
    model.processes["Load"] = model.processes["Load"]._replace(
        script="#Region Prologue\na = 1;\n#EndRegion"
    )
    service = FakeService()
    with pytest.raises(ModelError) as caught:
        deploy(model, service)
    assert "Prologue" in str(caught.value)
    assert service.writes == []


def test_a_datasource_password_stops_every_write(tm1py):
    model = load_model(MINI)
    model.processes["Load"] = model.processes["Load"]._replace(
        datasource={"Type": "ODBC", "password": "sw0rdf1sh"}
    )
    service = FakeService()
    with pytest.raises(ModelError) as caught:
        deploy(model, service)
    assert "Model source is committed to git" in str(caught.value)
    assert service.writes == []


def test_a_datasource_key_the_deployer_cannot_map_stops_every_write(tm1py):
    model = load_model(MINI)
    model.processes["Load"] = model.processes["Load"]._replace(
        datasource={"Type": "ASCII", "asciiMystery": 1}
    )
    service = FakeService()
    with pytest.raises(ModelError) as caught:
        deploy(model, service)
    assert "asciiMystery" in str(caught.value)
    assert service.writes == []


def test_a_cube_whose_rules_file_has_gone_stops_every_write(tm1py, tmp_path):
    model = load_model(MINI)
    model.cubes["Sales"] = model.cubes["Sales"]._replace(rules_source=tmp_path / "Gone.rules")
    service = FakeService()
    with pytest.raises(ModelError) as caught:
        deploy(model, service)
    assert "Gone.rules" in str(caught.value)
    assert service.writes == []


def test_a_refusal_that_needs_no_server_leaves_the_database_unread(tm1py):
    model = load_model(MINI)
    model.processes["Load"] = model.processes["Load"]._replace(
        script="#Region Prologue\na = 1;\n#EndRegion"
    )
    service = FakeService()
    with pytest.raises(ModelError):
        deploy(model, service)
    assert service.calls == []
