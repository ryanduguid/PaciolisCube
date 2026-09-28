"""Loading the manifest, cubes and processes of a whole model tree."""

import json
from pathlib import Path

import pytest

from conftest import write_model
from pacioliscube.cli import main
from pacioliscube.errors import EXIT_INVALID_MODEL
from pacioliscube.model import ModelError, load_cube, load_model

MINI = Path(__file__).parent / "fixtures" / "mini"


def write_manifest(root: Path, objects: str) -> None:
    (root / "tm1project.json").write_text(
        '{"Version": 1.0, "Name": "x", "Objects": %s}' % objects, encoding="utf-8"
    )


def test_model_loads_every_object_the_manifest_lists():
    model = load_model(MINI)
    assert set(model.dimensions) == {"Colour", "Measure"}
    assert set(model.cubes) == {"Sales", "Cost"}
    assert set(model.processes) == {"Load"}


def test_a_cube_records_its_dimensions_in_order():
    cube = load_model(MINI).cubes["Sales"]
    assert cube.dimensions == ("Colour", "Measure")


def test_a_cube_loads_on_its_own_from_the_git_native_layout():
    # Every cube in this layout links its dimensions as ../dimensions/X.json, so
    # a loader fenced to the cube's own folder refuses the very file it exists
    # to read, and reports it as a path climbing out of the model root.
    cube = load_cube(MINI / "cubes" / "Sales.json")
    assert cube.dimensions == ("Colour", "Measure")


def test_a_cube_with_rules_carries_a_parsed_ruleset():
    cube = load_model(MINI).cubes["Sales"]
    assert cube.rules is not None
    assert cube.rules.skipcheck is True


def test_a_cube_without_a_rules_link_carries_no_ruleset():
    assert load_model(MINI).cubes["Cost"].rules is None


def test_a_cube_linking_a_missing_rules_file_names_both_paths(tmp_path):
    cube_file = tmp_path / "Ghost.json"
    cube_file.write_text(
        '{"Name": "Ghost", "Dimensions@Code.links": ["Colour.json"],'
        ' "Rules@Code.link": "Ghost.rules"}',
        encoding="utf-8",
    )
    (tmp_path / "Colour.json").write_text(
        '{"Name": "Colour", "Hierarchies@Code.links": ["Colour.hierarchies/Colour.json"]}',
        encoding="utf-8",
    )
    with pytest.raises(ModelError) as caught:
        load_cube(cube_file)
    message = str(caught.value).replace("\\", "/")
    assert "Ghost.json" in message
    assert "Ghost.rules" in message


def test_process_script_is_read_from_its_linked_file():
    process = load_model(MINI).processes["Load"]
    assert "sVersion = pVersion;" in process.script
    assert process.parameters[0]["Name"] == "pVersion"
    assert process.datasource["Type"] == "ASCII"


def test_the_file_list_covers_every_object_and_its_linked_text():
    model = load_model(MINI)
    names = {path.name for path in model.files}
    assert {"tm1project.json", "Sales.json", "Sales.rules", "Load.json", "Load.ti"} <= names


def test_manifest_naming_a_missing_file_is_a_model_error(tmp_path):
    write_manifest(tmp_path, '{"Cubes": ["cubes/Ghost.json"]}')
    with pytest.raises(ModelError) as caught:
        load_model(tmp_path)
    assert "cubes/Ghost.json" in str(caught.value).replace("\\", "/")


def test_a_path_escaping_the_model_root_is_refused(tmp_path):
    root = tmp_path / "model"
    root.mkdir()
    (tmp_path / "outside.json").write_text('{"Name": "Outside"}', encoding="utf-8")
    write_manifest(root, '{"Cubes": ["../outside.json"]}')
    with pytest.raises(ModelError) as caught:
        load_model(root)
    assert "outside the model root" in str(caught.value)


def test_a_missing_manifest_names_the_file(tmp_path):
    with pytest.raises(ModelError) as caught:
        load_model(tmp_path)
    assert "tm1project.json" in str(caught.value).replace("\\", "/")


def test_malformed_json_names_the_file(tmp_path):
    (tmp_path / "tm1project.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ModelError) as caught:
        load_model(tmp_path)
    assert "invalid JSON" in str(caught.value)


OBJECT_LINKS = {
    "Dimensions": "dimensions/Colour.json",
    "Cubes": "cubes/Sales.json",
    "Processes": "processes/Load.json",
}


def add_manifest_alias(root, kind, *, same_path=False, name=None, reverse=False):
    original = root / OBJECT_LINKS[kind]
    duplicate = original if same_path else original.with_name("Alternate.json")
    if not same_path:
        payload = json.loads(original.read_text(encoding="utf-8"))
        if name is not None:
            payload["Name"] = name
        duplicate.write_text(json.dumps(payload), encoding="utf-8")
    path = root / "tm1project.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["Objects"][kind].append(duplicate.relative_to(root).as_posix())
    if reverse:
        manifest["Objects"][kind].reverse()
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return original, duplicate


@pytest.mark.parametrize("kind", ["Dimensions", "Cubes", "Processes"])
@pytest.mark.parametrize("same_path", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_duplicate_object_names_are_rejected_before_they_are_lost(
    tmp_path, kind, same_path, reverse,
):
    root = write_model(tmp_path, processes="x = 1;")
    original, duplicate = add_manifest_alias(root, kind, same_path=same_path, reverse=reverse)
    with pytest.raises(ModelError) as caught:
        load_model(root)
    message = str(caught.value)
    assert str(original) in message
    assert str(duplicate) in message
    assert json.loads(original.read_text(encoding="utf-8"))["Name"] in message


@pytest.mark.parametrize("reverse", [False, True])
def test_cube_names_must_be_distinct_under_cell_store_case_matching(tmp_path, reverse):
    root = write_model(tmp_path)
    original, duplicate = add_manifest_alias(root, "Cubes", name="sales", reverse=reverse)
    with pytest.raises(ModelError) as caught:
        load_model(root)
    assert "Sales" in str(caught.value)
    assert "sales" in str(caught.value)
    assert str(original) in str(caught.value)
    assert str(duplicate) in str(caught.value)


def test_unicode_cube_names_use_cell_store_case_folding(tmp_path):
    root = write_model(tmp_path)
    original = root / "cubes" / "Sales.json"
    payload = json.loads(original.read_text(encoding="utf-8"))
    payload["Name"] = "Straße"
    original.write_text(json.dumps(payload), encoding="utf-8")
    _, duplicate = add_manifest_alias(root, "Cubes", name="STRASSE")
    with pytest.raises(ModelError) as caught:
        load_model(root)
    for text in ("Straße", "STRASSE", str(original), str(duplicate)):
        assert text in str(caught.value)


@pytest.mark.parametrize("name", [1, True, ["Sales"], {"name": "Sales"}])
def test_cube_name_must_be_text_before_case_matching(tmp_path, name):
    root = write_model(tmp_path)
    path = root / "cubes" / "Sales.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["Name"] = name
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ModelError, match="Name must be a string") as caught:
        load_cube(path)
    assert str(path) in str(caught.value)


def test_object_names_in_different_kinds_keep_separate_namespaces(tmp_path):
    root = write_model(tmp_path, processes="x = 1;")
    for relative in ("cubes/Sales.json", "processes/Load.json"):
        path = root / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["Name"] = "Colour"
        path.write_text(json.dumps(payload), encoding="utf-8")
    model = load_model(root)
    assert "Colour" in model.dimensions
    assert "Colour" in model.cubes
    assert "Colour" in model.processes


@pytest.mark.parametrize("kind,name", [("Dimensions", "colour"), ("Processes", "load")])
def test_distinct_dimension_and_process_spelling_keeps_existing_lookup_rules(tmp_path, kind, name):
    root = write_model(tmp_path, processes="x = 1;")
    original, _ = add_manifest_alias(root, kind, name=name)
    model = load_model(root)
    objects = model.dimensions if kind == "Dimensions" else model.processes
    assert name in objects
    assert json.loads(original.read_text(encoding="utf-8"))["Name"] in objects


@pytest.mark.parametrize("name", ["Sales", "sales"])
@pytest.mark.parametrize("command", ["validate", "calculate"])
@pytest.mark.parametrize("reverse", [False, True])
def test_cli_rejects_ambiguous_cube_names_without_printing_a_result(
    tmp_path, capsys, name, command, reverse,
):
    root = write_model(
        tmp_path / "model",
        rules="SKIPCHECK; ['Amount'] = N: ['Units'] * ['Price']; FEEDERS; ['Units'] => ['Amount'];",
    )
    original, duplicate = add_manifest_alias(root, "Cubes", name=name, reverse=reverse)
    payload = json.loads(duplicate.read_text(encoding="utf-8"))
    payload["Rules@Code.link"] = "Alternate.rules"
    duplicate.write_text(json.dumps(payload), encoding="utf-8")
    duplicate.with_suffix(".rules").write_text(
        "SKIPCHECK; ['Amount'] = N: ['Units'] * ['Price'] * 2; FEEDERS; ['Units'] => ['Amount'];",
        encoding="utf-8",
    )
    data = tmp_path / "data"
    data.mkdir()
    # Six units at four each gives 24; the conflicting rule doubles that to 48.
    (data / "sales.csv").write_text(
        "Colour,Measure,Value\nRed,Units,6\nRed,Price,4\n", encoding="utf-8"
    )
    arguments = [command, str(root)]
    if command == "calculate":
        arguments.extend(["--data", str(data), "--cell", "Sales:Red,Amount"])
    assert main(arguments) == EXIT_INVALID_MODEL
    output = capsys.readouterr()
    assert output.out == ""
    assert str(original) in output.err
    assert str(duplicate) in output.err
