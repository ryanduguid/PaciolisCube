"""Loading the manifest, cubes and processes of a whole model tree."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import pacioliscube.model as model_module
from conftest import write_model
from pacioliscube.cli import main
from pacioliscube.errors import EXIT_INVALID_MODEL
from pacioliscube.model import ModelError, load_cube, load_dimension, load_model, load_process
from pacioliscube.validate import validate_model

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
    # The server writes a cube's dimensions as {"@id": "Dimensions('X')"}
    # references by name, so a cube read on its own needs no dimension file.
    cube = load_cube(MINI / "cubes" / "Sales.json")
    assert cube.dimensions == ("Colour", "Measure")


def test_a_cube_in_the_earlier_linked_form_still_loads_on_its_own(tmp_path):
    # Before it moved to the server's form, this model linked each dimension as
    # ../dimensions/X.json. A loader fenced to the cube's own folder would refuse
    # the very file it exists to read, as a path climbing out of the model root.
    root = write_model(tmp_path)
    assert load_cube(root / "cubes" / "Sales.json").dimensions == ("Colour", "Measure")


def reference(key: str) -> dict:
    """A cube's dimension reference as the server writes it, for an escaped key."""
    return {"@id": f"Dimensions('{key}')"}


def write_cube(root: Path, name: str = "Ghost", **fields) -> Path:
    path = root / f"{name}.json"
    path.write_text(json.dumps({"Name": name, **fields}), encoding="utf-8")
    return path


def test_a_doubled_quote_in_a_dimension_reference_reads_as_one_quote(tmp_path):
    path = write_cube(tmp_path, Dimensions=[reference("O''Brien")])
    assert load_cube(path).dimensions == ("O'Brien",)


@pytest.mark.parametrize(
    "entry",
    [
        "Colour",
        {},
        {"@id": "Hierarchies('Colour')"},
        {"@id": "Dimensions('')"},
        {"@id": "Dimensions('Colour')/Hierarchies('Colour')"},
    ],
)
def test_a_malformed_dimension_reference_is_refused(tmp_path, entry):
    with pytest.raises(ModelError, match="dimension reference"):
        load_cube(write_cube(tmp_path, Dimensions=[entry]))


def test_a_cube_giving_its_dimensions_both_ways_is_refused(tmp_path):
    root = write_model(tmp_path)
    path = write_cube(
        root / "cubes",
        Dimensions=[reference("Colour")],
        **{"Dimensions@Code.links": ["../dimensions/Colour.json"]},
    )
    with pytest.raises(ModelError, match="both as Dimensions and as Dimensions@Code.links"):
        load_cube(path)


@pytest.mark.parametrize("empty", [[], None])
def test_an_empty_dimensions_key_beside_links_is_still_both_ways(tmp_path, empty):
    # Presence decides the form: an empty or null Dimensions must not fall
    # through to the links as though it were absent.
    root = write_model(tmp_path)
    path = write_cube(
        root / "cubes",
        Dimensions=empty,
        **{"Dimensions@Code.links": ["../dimensions/Colour.json"]},
    )
    with pytest.raises(ModelError, match="both as Dimensions and as Dimensions@Code.links"):
        load_cube(path)


@pytest.mark.parametrize(
    "fields",
    [{}, {"Dimensions": []}, {"Dimensions": None}, {"Dimensions@Code.links": []}],
)
def test_a_cube_naming_no_dimensions_is_refused(tmp_path, fields):
    with pytest.raises(ModelError, match="names no dimensions"):
        load_cube(write_cube(tmp_path, **fields))


@pytest.mark.parametrize("form", ["Dimensions", "Dimensions@Code.links"])
def test_cube_dimensions_that_are_not_a_list_are_refused(tmp_path, form):
    with pytest.raises(ModelError, match=f"{form} must be a list"):
        load_cube(write_cube(tmp_path, **{form: reference("Colour")}))


def test_a_reference_to_a_dimension_the_model_lacks_fails_validation(tmp_path):
    # A name carries no file to check at load time, so the validator reports it.
    root = write_model(tmp_path)
    write_cube(root / "cubes", "Sales", Dimensions=[reference("Colour"), reference("Ghost")])
    codes = {finding.code for finding in validate_model(load_model(root))}
    assert "DIM001" in codes


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


def test_an_integer_over_the_digit_limit_is_a_model_error_not_a_traceback(tmp_path):
    (tmp_path / "tm1project.json").write_text('{"Name": ' + "9" * 5000 + "}", encoding="utf-8")
    with pytest.raises(ModelError) as caught:
        load_model(tmp_path)
    assert "invalid JSON" in str(caught.value)


def test_json_nested_too_deeply_is_a_model_error_not_a_traceback(tmp_path, monkeypatch):
    # How deep the parser can go depends on the platform's stack, so the
    # decoder's RecursionError is raised directly rather than by a fixture.
    (tmp_path / "tm1project.json").write_text("{}", encoding="utf-8")

    def too_deep(*args, **kwargs):
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")

    monkeypatch.setattr(model_module.json, "loads", too_deep)
    with pytest.raises(ModelError, match="nested too deeply"):
        load_model(tmp_path)


OBJECT_LINKS = {
    "Dimensions": "dimensions/Colour.json",
    "Cubes": "cubes/Sales.json",
    "Processes": "processes/Load.json",
}


@pytest.mark.parametrize("kind", list(OBJECT_LINKS))
@pytest.mark.parametrize("value", [None, True, False, 1, 1.5, "", "not-a-list", {}])
def test_manifest_object_collections_must_be_lists(tmp_path, capsys, kind, value):
    write_manifest(tmp_path, json.dumps({kind: value}))
    message = f"Objects.{kind} must be a list"

    with pytest.raises(ModelError, match=message):
        load_model(tmp_path)
    assert main(["validate", str(tmp_path)]) == EXIT_INVALID_MODEL
    output = capsys.readouterr()
    assert output.out == ""
    assert message in output.err
    assert "Traceback" not in output.err


@pytest.mark.parametrize("kind", list(OBJECT_LINKS))
def test_manifest_link_mappings_cannot_stand_in_for_lists(tmp_path, capsys, kind):
    root = write_model(tmp_path, processes="x = 1;")
    path = root / "tm1project.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["Objects"][kind] = {link: {} for link in manifest["Objects"][kind]}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    message = f"Objects.{kind} must be a list"

    with pytest.raises(ModelError, match=message):
        load_model(root)
    assert main(["validate", str(root)]) == EXIT_INVALID_MODEL
    output = capsys.readouterr()
    assert output.out == ""
    assert message in output.err


@pytest.mark.parametrize("kind", list(OBJECT_LINKS))
@pytest.mark.parametrize("member", [None, True, 1, {}, []])
def test_manifest_link_members_must_be_strings(tmp_path, capsys, kind, member):
    write_manifest(tmp_path, json.dumps({kind: [member]}))

    with pytest.raises(ModelError, match="link must be a string"):
        load_model(tmp_path)
    assert main(["validate", str(tmp_path)]) == EXIT_INVALID_MODEL
    output = capsys.readouterr()
    assert output.out == ""
    assert "link must be a string" in output.err


@pytest.mark.parametrize(
    "objects", [{}, {kind: [] for kind in OBJECT_LINKS}, {"Views": None}, {"Views": 1}]
)
def test_absent_and_empty_manifest_collections_remain_valid(tmp_path, capsys, objects):
    write_manifest(tmp_path, json.dumps(objects))

    model = load_model(tmp_path)
    assert model.dimensions == model.cubes == model.processes == {}
    assert main(["validate", str(tmp_path)]) == 0
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("kind", ["Cubes", "Processes"])
def test_manifest_collection_types_are_checked_before_resolving_links(tmp_path, monkeypatch, kind):
    write_manifest(tmp_path, json.dumps({"Dimensions": ["dimensions/Colour.json"], kind: None}))

    def refuse_resolution(*args, **kwargs):
        pytest.fail("A malformed collection must be rejected before resolving any object link")

    monkeypatch.setattr("pacioliscube.model._resolve_link", refuse_resolution)
    with pytest.raises(ModelError, match=f"Objects.{kind} must be a list"):
        load_model(tmp_path)


@pytest.mark.parametrize(
    "relative,field,value,loader",
    [
        ("dimensions/Colour.json", "Hierarchies@Code.links", [None], load_dimension),
        ("cubes/Sales.json", "Dimensions@Code.links", [None], load_cube),
        ("cubes/Sales.json", "Rules@Code.link", True, load_cube),
        ("processes/Load.json", "Code@Code.link", True, load_process),
    ],
)
def test_nested_link_values_use_the_same_model_error(tmp_path, capsys, relative, field, value, loader):
    root = write_model(tmp_path, processes="x = 1;")
    path = root / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    for load, source in ((loader, path), (load_model, root)):
        with pytest.raises(ModelError, match="link must be a string") as caught:
            load(source)
        assert str(path) in str(caught.value)
    assert main(["validate", str(root)]) == EXIT_INVALID_MODEL
    output = capsys.readouterr()
    assert output.out == ""
    assert str(path) in output.err


@pytest.mark.parametrize(
    "value",
    [False, True, 0, 1, 1.5, "", "Colour.hierarchies/Colour.json", {}, {"Colour.hierarchies/Colour.json": False}],
)
def test_hierarchy_link_collection_must_be_a_list(tmp_path, capsys, value):
    root = write_model(tmp_path)
    path = root / "dimensions/Colour.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["Hierarchies@Code.links"] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    for loader, source in ((load_dimension, path), (load_model, root)):
        with pytest.raises(ModelError, match="Hierarchies@Code[.]links must be a list") as caught:
            loader(source)
        assert str(path) in str(caught.value)
    assert main(["validate", str(root)]) == EXIT_INVALID_MODEL
    output = capsys.readouterr()
    assert output.out == ""
    assert "Hierarchies@Code.links must be a list" in output.err
    assert str(path) in output.err


@pytest.mark.parametrize(
    "relative,field,loader",
    [
        ("cubes/Sales.json", "Rules@Code.link", load_cube),
        ("processes/Load.json", "Code@Code.link", load_process),
    ],
)
@pytest.mark.parametrize("value", [False, 0, 0.0, [], {}])
def test_falsey_optional_link_values_must_be_strings(tmp_path, capsys, relative, field, loader, value):
    root = write_model(tmp_path, processes="x = 1;" if loader is load_process else "")
    if loader is load_process:
        (root / "processes/Load.ti").unlink()
    path = root / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")

    for load, source in ((loader, path), (load_model, root)):
        with pytest.raises(ModelError, match="link must be a string") as caught:
            load(source)
        assert str(path) in str(caught.value)
    assert main(["validate", str(root)]) == EXIT_INVALID_MODEL
    output = capsys.readouterr()
    assert output.out == ""
    assert "link must be a string" in output.err
    assert str(path) in output.err


@pytest.mark.parametrize("state", ["omitted", "null", "empty"])
def test_optional_links_preserve_no_source_states(tmp_path, capsys, state):
    root = write_model(tmp_path, processes="x = 1;")
    (root / "processes/Load.ti").unlink()
    for relative, field in (
        ("cubes/Sales.json", "Rules@Code.link"),
        ("processes/Load.json", "Code@Code.link"),
    ):
        path = root / relative
        payload = json.loads(path.read_text(encoding="utf-8"))
        if state == "omitted":
            payload.pop(field, None)
        else:
            payload[field] = None if state == "null" else ""
        path.write_text(json.dumps(payload), encoding="utf-8")
    cube = load_cube(root / "cubes/Sales.json")
    process = load_process(root / "processes/Load.json")
    assert cube.rules is None and cube.rules_source is None
    assert process.script == "" and process.script_source is None
    model = load_model(root)
    assert model.cubes["Sales"].rules_source is None
    assert model.processes["Load"].script_source is None
    assert {path.name for path in model.files}.isdisjoint({"Sales.rules", "Load.ti"})
    assert main(["validate", str(root)]) == 0
    output = capsys.readouterr()
    assert output.out == "0 errors, 0 warnings\n"
    assert output.err == ""


@pytest.mark.parametrize("state", ["omitted", "null", "empty"])
def test_hierarchy_links_preserve_no_hierarchy_error(tmp_path, state):
    root = write_model(tmp_path)
    path = root / "dimensions/Colour.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if state == "omitted":
        payload.pop("Hierarchies@Code.links")
    else:
        payload["Hierarchies@Code.links"] = None if state == "null" else []
    path.write_text(json.dumps(payload), encoding="utf-8")
    for loader, source in ((load_dimension, path), (load_model, root)):
        with pytest.raises(ModelError, match="links no hierarchy file"):
            loader(source)


@pytest.mark.parametrize("mapping", [False, True])
def test_invalid_hierarchy_collection_is_refused_before_following_links(tmp_path, monkeypatch, mapping):
    root = write_model(tmp_path)
    path = root / "dimensions/Colour.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    link = "Colour.hierarchies/Colour.json"
    payload["Hierarchies@Code.links"] = {link: False} if mapping else link
    path.write_text(json.dumps(payload), encoding="utf-8")
    original_resolve = model_module._resolve_link

    def resolve(base, link, root=None):
        assert base != path, "malformed hierarchy collection reached link resolution"
        return original_resolve(base, link, root)

    monkeypatch.setattr(model_module, "_resolve_link", resolve)
    for loader, source in ((load_dimension, path), (load_model, root)):
        with pytest.raises(ModelError, match="Hierarchies@Code[.]links must be a list"):
            loader(source)


@pytest.mark.parametrize(
    "relative,field,value,message",
    [
        ("dimensions/Colour.json", "Hierarchies@Code.links", True, "Hierarchies@Code.links must be a list"),
        ("cubes/Sales.json", "Rules@Code.link", False, "link must be a string"),
    ],
)
def test_invalid_nested_link_shape_exits_the_cli_process(tmp_path, relative, field, value, message):
    root = write_model(tmp_path)
    path = root / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload[field] = value
    path.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "pacioliscube.cli", "validate", str(root)],
        capture_output=True, text=True,
    )
    assert result.returncode == EXIT_INVALID_MODEL
    assert result.stdout == ""
    assert message in result.stderr
    assert str(path) in result.stderr
    assert "Traceback" not in result.stderr


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


NAME_FIELDS = [
    ("dimensions/Colour.json", ("Name",)),
    ("dimensions/Colour.hierarchies/Colour.json", ("Name",)),
    ("dimensions/Colour.hierarchies/Colour.json", ("Elements", 0, "Name")),
    ("dimensions/Colour.hierarchies/Colour.json", ("Edges", 0, "ParentName")),
    ("dimensions/Colour.hierarchies/Colour.json", ("Edges", 0, "ComponentName")),
    ("processes/Load.json", ("Name",)),
]


def replace_model_name(root, relative, keys, name):
    path = root / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    item = payload
    for key in keys[:-1]:
        item = item[key]
    item[keys[-1]] = name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize("relative,keys", NAME_FIELDS)
@pytest.mark.parametrize("name", [42, True, ["bad-name"], {"name": "bad-name"}])
def test_model_names_must_be_text_at_the_loading_boundary(tmp_path, relative, keys, name):
    root = write_model(tmp_path, processes="x = 1;")
    path = replace_model_name(root, relative, keys, name)
    with pytest.raises(ModelError, match="must be") as caught:
        load_model(root)
    assert str(path) in str(caught.value)
    assert keys[-1] in str(caught.value)


@pytest.mark.parametrize("name", [42, True, ["bad-name"], {"name": "bad-name"}])
def test_standalone_cube_rejects_non_text_linked_dimension_names(tmp_path, name):
    root = write_model(tmp_path)
    path = replace_model_name(root, "dimensions/Colour.json", ("Name",), name)
    with pytest.raises(ModelError, match="Name must be a string") as caught:
        load_cube(root / "cubes" / "Sales.json")
    assert str(path) in str(caught.value)


@pytest.mark.parametrize("name", [None, "", 0, False, [], {}])
def test_standalone_cube_preserves_falsey_linked_name_fallback(tmp_path, name):
    root = write_model(tmp_path)
    replace_model_name(root, "dimensions/Colour.json", ("Name",), name)
    assert load_cube(root / "cubes" / "Sales.json").dimensions == ("Colour", "Measure")


@pytest.mark.parametrize("name", ["Straße", " Colour "])
def test_standalone_cube_preserves_linked_name_spelling(tmp_path, name):
    root = write_model(tmp_path)
    replace_model_name(root, "dimensions/Colour.json", ("Name",), name)
    assert load_cube(root / "cubes" / "Sales.json").dimensions == (name, "Measure")


@pytest.mark.parametrize("command", ["validate", "calculate"])
@pytest.mark.parametrize("relative,keys,name", [
    ("processes/Load.json", ("Name",), 42),
    ("dimensions/Colour.json", ("Name",), ["bad-name"]),
    ("dimensions/Colour.hierarchies/Colour.json", ("Edges", 0, "ParentName"), 42),
])
def test_cli_reports_non_text_names_as_invalid_models(
    tmp_path, capsys, command, relative, keys, name,
):
    root = write_model(tmp_path / "model", processes="x = 1;")
    path = replace_model_name(root, relative, keys, name)
    arguments = [command, str(root)]
    if command == "calculate":
        data = tmp_path / "data"
        data.mkdir()
        (data / "sales.csv").write_text(
            "Colour,Measure,Value\nRed,Units,6\n", encoding="utf-8"
        )
        arguments.extend(["--data", str(data), "--cell", "Sales:Red,Units"])
    assert main(arguments) == EXIT_INVALID_MODEL
    output = capsys.readouterr()
    assert output.out == ""
    assert str(path) in output.err
    assert "Traceback" not in output.err


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
