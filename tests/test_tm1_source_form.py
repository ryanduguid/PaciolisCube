"""The model's source keeps the form of IBM's TM1 source specification.

IBM's TM1 source specification and a published sample model
(Hubert-Heijkers/tm1-model-MiniSData) give a cube's dimensions as
``{"@id": "Dimensions('X')"}`` references and a process's code as one ``.ti``
file with ``#region`` blocks for the Prolog, Metadata, Data and Epilog
procedures. tm1gitpy, a TM1 Git reimplementation, also requires
``HasSecurityAccess``; no process here touches security data, so each declares
``false``. The first tests hold the shipped model to that form.

The last test loads the model with tm1gitpy as well and requires both readers
to agree. It needs the ``tm1git`` dependency group, so it is skipped without
it, unless ``PACIOLISCUBE_REQUIRE_TM1GITPY=1`` makes a missing tm1gitpy a
failure, as the CI job that installs the group does.
"""

import json
import os
import re
from decimal import Decimal

import pytest

from conftest import MODEL_ROOT
from pacioliscube.model import load_model

PROCEDURES = ("Prolog", "Metadata", "Data", "Epilog")
MARKERS = [line for name in PROCEDURES for line in (f"#region {name}", "#endregion")]
REGION_LINE = re.compile(r"^#(region|endregion)\b", re.IGNORECASE)


def sources(folder: str, pattern: str):
    paths = sorted((MODEL_ROOT / folder).glob(pattern))
    assert paths, f"no {pattern} under model/{folder}"
    return paths


def test_every_cube_names_its_dimensions_as_the_server_writes_them():
    for path in sources("cubes", "*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "Dimensions@Code.links" not in payload, path.name
        assert payload["Dimensions"], path.name
        for entry in payload["Dimensions"]:
            assert re.fullmatch(r"Dimensions\('[^']+'\)", entry["@id"]), (path.name, entry)


def test_every_process_declares_security_access_and_links_its_code():
    for path in sources("processes", "*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload.get("HasSecurityAccess"), bool), path.name
        assert payload.get("Code@Code.link") == f"{path.stem}.ti", path.name


def test_every_script_has_the_four_procedure_regions_in_server_order():
    for path in sources("processes", "*.ti"):
        lines = path.read_text(encoding="utf-8").splitlines()
        assert lines[0] == "#region Prolog", path.name
        assert [line for line in lines if REGION_LINE.match(line)] == MARKERS, path.name


def tm1gitpy_deserializer():
    """tm1gitpy's deserializer, skipping or failing when it is not installed."""
    if os.environ.get("PACIOLISCUBE_REQUIRE_TM1GITPY") == "1":
        from tm1_git_py.services import deserializer
    else:
        deserializer = pytest.importorskip("tm1_git_py.services.deserializer")
    return deserializer


def test_tm1gitpy_reads_the_model_the_way_pacioliscube_does(tmp_path, monkeypatch):
    deserializer = tm1gitpy_deserializer()
    # tm1gitpy keeps a SQLite cache under .tm1gitpy/ in the working directory.
    monkeypatch.chdir(tmp_path)
    theirs, errors = deserializer.deserialize_model(str(MODEL_ROOT), max_workers=2)
    assert errors == {}
    ours = load_model(MODEL_ROOT)
    tree = theirs.to_dict()

    assert {dimension["name"] for dimension in tree["dimensions"]} == set(ours.dimensions)
    for dimension in tree["dimensions"]:
        [hierarchy] = dimension["hierarchies"]
        mine = ours.dimensions[dimension["name"]].default_hierarchy
        assert hierarchy["name"] == mine.name
        assert {(e["Name"], e["Type"]) for e in hierarchy["elements"]} == {
            (element.name, element.element_type) for element in mine.elements.values()
        }
        assert {
            (e["ParentName"], e["ComponentName"], Decimal(str(e.get("Weight", 1))))
            for e in hierarchy["edges"]
        } == {(edge.parent, edge.component, edge.weight) for edge in mine.edges}

    assert {cube["name"]: tuple(cube["dimensions"]) for cube in tree["cubes"]} == {
        name: cube.dimensions for name, cube in ours.cubes.items()
    }
    assert {process["name"]: process["code_link"] for process in tree["processes"]} == {
        name: process.script_source.name for name, process in ours.processes.items()
    }
