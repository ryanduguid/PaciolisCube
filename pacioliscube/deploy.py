"""Push a loaded model to an IBM Planning Analytics database through TM1py.

Every other module in this package is offline. This one is the single place
that can write to a server, and it is optional: TM1py is imported inside the
functions that need it, so importing ``pacioliscube`` never pulls in a server
library, and the parser, the evaluator and the validator keep working without
one.

Read this before you run it. ``deploy`` is a convenience for a model you
already trust, not a release gate. It writes to whichever database the
``TM1Service`` you hand it is connected to, with whatever credentials that
service holds, and it cannot tell a sandbox from production. Run
``validate_model`` first, read ``plan`` second, and point the service at a
database you are willing to overwrite.

What it will not do:

- It never calls a delete endpoint, so a dimension, hierarchy, cube or process
  that the model does not name survives untouched. Inside an object the model
  does name, an update replaces content: the elements and edges of a hierarchy
  become the ones the model declares, and elements the model dropped go with
  them.
- It refuses a deployment where a cube already exists with a different
  dimension list. What a server does with such a change has not been confirmed
  here, and there are two candidates: TM1 rejects it, leaving the deployment
  stopped partway, or TM1 takes it and the cube's data does not survive. Neither
  is worth finding out on a database with numbers in it, so the deployer stops
  instead. This bullet is the only place that reason is given, so no message or
  comment elsewhere has to commit to a mechanism nobody has tested.
- It leaves the rules of a server cube alone when the model gives that cube no
  rules, rather than clearing them.
- It never carries a datasource password out of model source, and refuses a
  model that puts one there.
- It writes no cell data and runs none of the model's processes. Deploying the
  objects is the whole job. One exception belongs to TM1py rather than to this
  deployer: some Planning Analytics versions cannot take a hierarchy's edges in
  the same request as the hierarchy, so on those versions updating an existing
  hierarchy makes TM1py run a short TurboIntegrator snippet of its own to add
  them. TM1py 2.3.1 lists them in
  ``TM1py.Services.HierarchyService.EDGES_WORKAROUND_VERSIONS`` as 11.0.002,
  11.0.003 and 11.1.000. Deploying to one of those does execute TI code.

A refusal never leaves a half applied database. Everything a refusal needs is
in hand before the first write: the model is checked against itself, every
object is translated in full, and only then is the server read. A translation
that cannot be done, such as a rules file that has gone or a #Region name
outside the four, stops the deployment while the database is still untouched.

Translation reads two things from disk that the loaded ``Model`` does not keep:
the rule text beside a cube, and the ``Variables`` block beside a process. The
offline engine has no use for either, so ``model.py`` drops both, and a
deployment cannot do without them.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Iterable, NamedTuple, Optional

from pacioliscube.model import Cube, Dimension, Model, ModelError, Process

TM1PY_MISSING = (
    "TM1py is not installed. It is an optional extra, because everything else in "
    "pacioliscube runs offline. Install it with: pip install 'pacioliscube[deploy]'"
)

DIMENSION = "dimension"
CUBE = "cube"
PROCESS = "process"

CREATED = "created"
UPDATED = "updated"
UNCHANGED = "unchanged"

PROCEDURES = ("prolog", "metadata", "data", "epilog")

# The TM1 OData names for datasource settings, mapped to the keyword arguments
# TM1py's Process constructor takes. A password is deliberately absent: model
# source is committed to git, so a password does not belong in it.
DATASOURCE_ARGUMENTS = {
    "Type": "datasource_type",
    "asciiDecimalSeparator": "datasource_ascii_decimal_separator",
    "asciiDelimiterChar": "datasource_ascii_delimiter_char",
    "asciiDelimiterType": "datasource_ascii_delimiter_type",
    "asciiHeaderRecords": "datasource_ascii_header_records",
    "asciiQuoteCharacter": "datasource_ascii_quote_character",
    "asciiThousandSeparator": "datasource_ascii_thousand_separator",
    "dataSourceNameForClient": "datasource_data_source_name_for_client",
    "dataSourceNameForServer": "datasource_data_source_name_for_server",
    "userName": "datasource_user_name",
    "query": "datasource_query",
    "usesUnicode": "datasource_uses_unicode",
    "view": "datasource_view",
    "subset": "datasource_subset",
    "jsonRootPointer": "datasource_json_root_pointer",
    "jsonVariableMapping": "datasource_json_variable_mapping",
}


class DeploymentError(RuntimeError):
    """Raised when a deployment is refused, before anything has been written."""


class Procedures(NamedTuple):
    prolog: str
    metadata: str
    data: str
    epilog: str


class Operation(NamedTuple):
    """One step a deployment would take, as plain data and with no server."""

    kind: str
    name: str
    summary: str
    source: str
    depends_on: tuple[str, ...]


class Change(NamedTuple):
    """One step a deployment did take, and what it did."""

    kind: str
    name: str
    action: str
    detail: str


def _import_tm1py():
    """Import TM1py, or explain how to get it.

    The import sits inside a function rather than at the top of the module so
    that a user who never deploys never needs the library, and so that the one
    who does gets an instruction instead of a traceback about a missing name.
    """
    try:
        import TM1py
    except ImportError as error:
        raise ImportError(TM1PY_MISSING) from error
    return TM1py


def _key(name: str) -> str:
    """Fold a name the way TM1 compares them, ignoring case and spaces."""
    return name.replace(" ", "").casefold()


def _plural(count: int, noun: str, plural: Optional[str] = None) -> str:
    if count == 1:
        return f"1 {noun}"
    return f"{count} {plural or noun + 's'}"


def _join(names: Iterable[str]) -> str:
    names = list(names)
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def split_procedures(script: str, source: object = "") -> Procedures:
    """Split a git native ``.ti`` file into the four TurboIntegrator procedures.

    A ``.ti`` file published by TM1 wraps each procedure in a ``#Region Prolog``
    and ``#EndRegion`` pair. Those markers are structure rather than code, so
    they are dropped. Text sitting outside every region joins the prolog, which
    is where a TurboIntegrator process opens. A region name outside the four is
    an error rather than a silent omission, because dropping a procedure would
    deploy a process that quietly does less than its source says.
    """
    collected: dict[str, list[str]] = {name: [] for name in PROCEDURES}
    current: Optional[str] = None
    # The label as the file spells it and the line it opened on, kept alongside
    # the folded key, so a refusal quotes the modeller's own text and points at
    # the line to edit rather than at wherever the file happened to run out.
    opened_label = ""
    opened_line = 0
    for number, line in enumerate(script.splitlines(), start=1):
        stripped = line.strip()
        lowered = stripped.casefold()
        if lowered.startswith("#region") and (len(stripped) == 7 or stripped[7].isspace()):
            label = stripped[7:].strip()
            if current is not None:
                raise ModelError(
                    f"{source} line {number}: #Region {label!r} opens inside #Region "
                    f"{opened_label!r} from line {opened_line}"
                )
            if label.casefold() not in collected:
                raise ModelError(
                    f"{source} line {number}: #Region {label!r} is not one of "
                    + ", ".join(PROCEDURES)
                )
            current = label.casefold()
            opened_label = label
            opened_line = number
            continue
        if lowered == "#endregion":
            if current is None:
                raise ModelError(f"{source} line {number}: #EndRegion without a #Region")
            current = None
            continue
        collected[current or "prolog"].append(line)
    if current is not None:
        raise ModelError(
            f"{source} line {opened_line}: #Region {opened_label!r} is never closed"
        )
    return Procedures(*("\n".join(collected[name]).strip() for name in PROCEDURES))


def _rule_text(cube: Cube) -> Optional[str]:
    """Reread the rule text beside a cube, which the parsed RuleSet does not hold.

    TM1 wants the file as the modeller wrote it, comments and layout included,
    so the text goes across untouched rather than being printed back from the
    expression tree.
    """
    if cube.rules_source is None:
        return None
    if not cube.rules_source.is_file():
        raise ModelError(f"{cube.rules_source}: rules file for cube {cube.name!r} is missing")
    return cube.rules_source.read_text(encoding="utf-8-sig")


def _variables(process: Process) -> tuple[dict, ...]:
    """Reread the Variables block beside a process.

    A loaded Process carries only what the offline engine needs, and variables
    are not part of that. A TurboIntegrator process reading a file needs them,
    so they come back off disk here. A process file that has since gone gives
    no variables rather than an error, because the script and the parameters
    are already in hand.
    """
    if not process.source.is_file():
        return ()
    try:
        payload = json.loads(process.source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ModelError(f"{process.source}: invalid JSON, {error}") from error
    return tuple(payload.get("Variables", ()))


def _datasource_arguments(process: Process) -> dict:
    """Map a model datasource block onto TM1py's Process keyword arguments.

    Only keys the model actually sets are passed on, so TM1py's own defaults
    stand for the rest. An unmapped key is an error: dropping it silently would
    deploy a process that reads its source differently from the way the model
    says it does.
    """
    arguments = {}
    for key, value in process.datasource.items():
        if key == "password":
            raise ModelError(
                f"{process.source}: the datasource of process {process.name!r} carries a "
                "password. Model source is committed to git, so the deployer will not carry "
                "it. Take it out of the model and set it on the server."
            )
        if key not in DATASOURCE_ARGUMENTS:
            raise ModelError(
                f"{process.source}: datasource key {key!r} is not one this deployer maps. "
                "Known keys are " + ", ".join(sorted(DATASOURCE_ARGUMENTS))
            )
        arguments[DATASOURCE_ARGUMENTS[key]] = value
    return arguments


def _weight(weight: Decimal) -> object:
    """Carry an edge weight across as a JSON number, which a Decimal is not.

    TM1 stores a weight as a double either way, so nothing is lost that would
    have survived the wire: an integral weight goes as an int, anything else as
    a float.
    """
    if weight == weight.to_integral_value():
        return int(weight)
    return float(weight)


def to_tm1py_dimension(dimension: Dimension):
    """Translate one loaded dimension, with every hierarchy it holds.

    Raises ImportError naming the ``deploy`` extra when TM1py is not installed.
    """
    tm1py = _import_tm1py()
    hierarchies = [
        tm1py.Hierarchy(
            name=hierarchy.name,
            dimension_name=dimension.name,
            elements=[
                tm1py.Element(element.name, element.element_type)
                for element in hierarchy.elements.values()
            ],
            edges={
                (edge.parent, edge.component): _weight(edge.weight) for edge in hierarchy.edges
            },
        )
        for hierarchy in dimension.hierarchies.values()
    ]
    return tm1py.Dimension(name=dimension.name, hierarchies=hierarchies)


def to_tm1py_cube(cube: Cube):
    """Translate one loaded cube, carrying its rule file text as written.

    Raises ImportError naming the ``deploy`` extra when TM1py is not installed.
    """
    tm1py = _import_tm1py()
    text = _rule_text(cube)
    return tm1py.Cube(
        name=cube.name,
        dimensions=list(cube.dimensions),
        rules=tm1py.Rules(text) if text is not None else None,
    )


def to_tm1py_process(process: Process):
    """Translate one loaded process into its four TurboIntegrator procedures.

    Raises ImportError naming the ``deploy`` extra when TM1py is not installed.
    """
    tm1py = _import_tm1py()
    procedures = split_procedures(process.script, process.script_source or process.source)
    return tm1py.Process(
        name=process.name,
        parameters=list(process.parameters),
        variables=list(_variables(process)),
        prolog_procedure=procedures.prolog,
        metadata_procedure=procedures.metadata,
        data_procedure=procedures.data,
        epilog_procedure=procedures.epilog,
        **_datasource_arguments(process),
    )


def _dimension_summary(dimension: Dimension) -> str:
    elements = sum(len(hierarchy.elements) for hierarchy in dimension.hierarchies.values())
    edges = sum(len(hierarchy.edges) for hierarchy in dimension.hierarchies.values())
    return ", ".join(
        (
            _plural(len(dimension.hierarchies), "hierarchy", "hierarchies"),
            _plural(elements, "element"),
            _plural(edges, "edge"),
        )
    )


def _cube_summary(cube: Cube) -> str:
    parts = [_plural(len(cube.dimensions), "dimension")]
    if cube.rules is None:
        parts.append("no rules")
    else:
        parts.append(_plural(len(cube.rules.rules), "rule"))
        parts.append(_plural(len(cube.rules.feeders), "feeder"))
    return ", ".join(parts)


def _process_summary(process: Process) -> str:
    procedures = split_procedures(process.script, process.script_source or process.source)
    filled = [name for name, text in zip(PROCEDURES, procedures) if text]
    return ", ".join(
        (
            _plural(len(process.parameters), "parameter"),
            f"{process.datasource.get('Type', 'None')} datasource",
            f"code in {_join(filled)}" if filled else "no code",
        )
    )


def _cubes_the_script_names(process: Process, model: Model) -> tuple[str, ...]:
    """Which model cubes a process script quotes, as a hint for a reader only.

    A TurboIntegrator function names its cube as a quoted literal, so a quoted
    match is a fair guide to what a process touches. It drives nothing: the
    deployment order is fixed, and a cube built by a variable goes unnoticed.
    """
    script = process.script.casefold()
    return tuple(name for name in model.cubes if f"'{name}'".casefold() in script)


def plan(model: Model) -> tuple[Operation, ...]:
    """Return, in order, what deploying this model would do. No server, no TM1py.

    Dimensions come first because a cube cannot be built before them, cubes
    come next because a process addresses cubes, and processes come last. This
    reports intent only: it reads nothing from a server, so it cannot say
    whether a given object already exists there.
    """
    operations: list[Operation] = []
    for dimension in model.dimensions.values():
        operations.append(
            Operation(
                DIMENSION,
                dimension.name,
                _dimension_summary(dimension),
                str(dimension.source),
                (),
            )
        )
    for cube in model.cubes.values():
        operations.append(
            Operation(CUBE, cube.name, _cube_summary(cube), str(cube.source), tuple(cube.dimensions))
        )
    for process in model.processes.values():
        operations.append(
            Operation(
                PROCESS,
                process.name,
                _process_summary(process),
                str(process.source),
                _cubes_the_script_names(process, model),
            )
        )
    return tuple(operations)


class _Targets(NamedTuple):
    """Every object of a model translated for TM1py, keyed by its model name."""

    dimensions: dict
    cubes: dict
    processes: dict


def _translate(model: Model) -> _Targets:
    """Translate the whole model into TM1py objects, touching no server.

    Translation is where most refusals live: a #Region name outside the four, a
    datasource password, a datasource key this deployer does not map, a rules
    file that has gone off disk. None of them need a server to reach, so a
    deployment does all of the translating before it writes anything. Translating
    object by object inside the write loop instead lets the last object of a
    model refuse a deployment whose earlier objects are already on the database.
    """
    return _Targets(
        {name: to_tm1py_dimension(dimension) for name, dimension in model.dimensions.items()},
        {name: to_tm1py_cube(cube) for name, cube in model.cubes.items()},
        {name: to_tm1py_process(process) for name, process in model.processes.items()},
    )


def _check_model(model: Model) -> None:
    """Refuse a model that contradicts itself. Reads nothing, server or disk."""
    for cube in model.cubes.values():
        missing = [name for name in cube.dimensions if name not in model.dimensions]
        if missing:
            raise DeploymentError(
                f"cube {cube.name!r} names {_join(repr(name) for name in missing)}, which the "
                "model does not hold, so the cube cannot be built. Run validate_model."
            )


def _check_server(model: Model, tm1) -> None:
    """Refuse a deployment the database will not take, before the first write.

    This is the only check that needs the server, so it runs last of the three
    and reads only. The module docstring carries the reason a cube whose
    dimensions differ is refused rather than changed.
    """
    for cube in model.cubes.values():
        if not tm1.cubes.exists(cube.name):
            continue
        existing = tuple(tm1.cubes.get_dimension_names(cube.name))
        if [_key(name) for name in existing] != [_key(name) for name in cube.dimensions]:
            raise DeploymentError(
                f"cube {cube.name!r} exists on the server over {_join(existing)}, but the model "
                f"gives it {_join(cube.dimensions)}. This deployer will not change the dimension "
                "list of a cube that already exists, so the deployment is refused. Change the "
                "model to match, or drop the cube on the server yourself."
            )


def _deploy_dimension(dimension: Dimension, target, tm1) -> Change:
    summary = _dimension_summary(dimension)
    if not tm1.dimensions.exists(dimension.name):
        tm1.dimensions.create(target)
        return Change(DIMENSION, dimension.name, CREATED, summary)
    # Updating the dimension itself would drop any hierarchy the model does not
    # name, so each hierarchy is written on its own instead. Existing element
    # attributes are kept for the same reason: the model declares none, and an
    # empty list would otherwise wipe the ones a server already carries.
    for hierarchy in target.hierarchies:
        if tm1.hierarchies.exists(dimension.name, hierarchy.name):
            # On the Planning Analytics versions TM1py lists in
            # HierarchyService.EDGES_WORKAROUND_VERSIONS this call runs a
            # TurboIntegrator snippet of TM1py's own to add the edges. The
            # module docstring says so, because it is the one place a
            # deployment executes anything.
            tm1.hierarchies.update(hierarchy, keep_existing_attributes=True)
        else:
            tm1.hierarchies.create(hierarchy)
    # Reported as updated because a write went out, not because the content
    # differed. Telling those apart would mean reading every element and edge
    # back off the server first, which this deployer does not do.
    return Change(DIMENSION, dimension.name, UPDATED, summary)


def _deploy_cube(cube: Cube, target, tm1) -> Change:
    summary = _cube_summary(cube)
    if not tm1.cubes.exists(cube.name):
        tm1.cubes.create(target)
        return Change(CUBE, cube.name, CREATED, summary)
    if target.rules is None:
        return Change(
            CUBE,
            cube.name,
            UNCHANGED,
            "the model gives this cube no rules, so whatever rules the server holds are left alone",
        )
    # The dimensions already match, checked before anything was written, so the
    # rules are the only part left to send. Patching the whole cube would resend
    # a dimension list that has not changed.
    tm1.cubes.update_or_create_rules(cube.name, target.rules)
    return Change(CUBE, cube.name, UPDATED, summary)


def _deploy_process(process: Process, target, tm1) -> Change:
    summary = _process_summary(process)
    if tm1.processes.exists(process.name):
        tm1.processes.update(target)
        return Change(PROCESS, process.name, UPDATED, summary)
    tm1.processes.create(target)
    return Change(PROCESS, process.name, CREATED, summary)


def deploy(model: Model, tm1) -> tuple[Change, ...]:
    """Create or update every object of a model on the database ``tm1`` reaches.

    ``tm1`` is a connected ``TM1py.TM1Service`` and has no default, so a
    deployment always names the database it is aimed at. Three things happen
    before the first write: the model is checked against itself, every object is
    translated, and the server is read to see whether it will take them. A
    refusal from any of the three leaves the database as it found it.

    Objects then go in dependency order, dimensions then cubes then processes.

    Returns one Change per object. ``created`` and ``updated`` name the endpoint
    the object went to, not whether its content differed from what the server
    already held: nothing is read back to compare, so an object that already
    exists is reported ``updated`` even where the write changed nothing.
    ``unchanged`` is reported only where nothing was sent at all, which today is
    the existing cube the model gives no rules.

    Raises DeploymentError when a check refuses the deployment, ModelError when
    an object cannot be translated, and ImportError naming the ``deploy`` extra
    when TM1py is not installed.
    """
    # Ahead of everything, so a machine without TM1py says so rather than
    # reporting a successful deployment of a model that holds no objects.
    _import_tm1py()
    _check_model(model)
    targets = _translate(model)
    _check_server(model, tm1)
    changes: list[Change] = []
    for name, dimension in model.dimensions.items():
        changes.append(_deploy_dimension(dimension, targets.dimensions[name], tm1))
    for name, cube in model.cubes.items():
        changes.append(_deploy_cube(cube, targets.cubes[name], tm1))
    for name, process in model.processes.items():
        changes.append(_deploy_process(process, targets.processes[name], tm1))
    return tuple(changes)
