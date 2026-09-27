# v0.1.3

- Adds `pacioliscube explain`, which prints JSON evidence for how each requested cell is calculated: rule locations, inputs, arithmetic, IF branches and weighted contributions. The README documented it before any release carried it.
- Adds `pacioliscube compare`, which compares two data snapshots of the same model and keeps both explain results and the differences in the selected cells. It is an offline comparison; native TM1 agreement remains unverified.
- Feeder validation pairs each `DB()` coordinate with its own dimension of the target cube and compares whole feeder areas, so a swapped coordinate or a feeder aimed at the wrong slice is reported.
- A rule expression or weighted consolidation whose arithmetic overflows raises an `EvaluationError` naming its cube and coordinate, and a rules file outside the grammar exits with the invalid-model code 2 instead of a traceback.
- The `ClearVersion` and `ExportPnL` processes create their view and subsets as temporary objects, so concurrent runs no longer share them.
- The build backend is pinned to hatchling 1.32.0, and publication requires the component checks for the exact release commit.

# v0.1.2

- Publishes the attested wheel and source distribution to PyPI as `pacioliscube` through trusted publishing.
- No functional change since v0.1.1.

# v0.1.1

- The repository was renamed from PaciolisCube to planning-analytics-model, and the README now says the package is installed from source rather than from PyPI.
- The CSV loader refuses 2 rows that give the same cell different values and names both rows, and the loader and the model reader reject malformed consolidation coordinates and unusable temporary TM1 objects.
- A command that prints several cells shares one set of calculation caches across them instead of recomputing the model per cell.
- The command line reads its version from the installed package metadata rather than from a second copy of the number.
- Continuous integration gained ruff and mypy, Python 3.12 and 3.13, pinned action SHAs, job timeouts and concurrency groups, changed-line branch coverage over the validation logic, and a check that refuses a pull request carrying an AI authorship credit.
- The model's feeders now cover monthly depreciation, feeder arity is checked, and `docs/native-comparison.md` records native TM1 agreement as pending with the evidence a comparison would need.
