"""Benchmark fingerprints and sample lifetimes must support the reported claims."""

from benchmarks import evaluate as benchmark
from pacioliscube.evaluate import CellStore


def test_fingerprint_preserves_container_types():
    assert benchmark.fingerprint((1, 2)) != benchmark.fingerprint([1, 2])
    assert benchmark.fingerprint(CellStore()) != benchmark.fingerprint([])
    assert benchmark.fingerprint({"coordinate": ("Red",)}) != benchmark.fingerprint({
        "coordinate": ["Red"],
    })


def test_preceding_result_teardown_is_outside_the_next_sample(monkeypatch):
    clock = [0]

    class Result(dict):
        def __del__(self):
            clock[0] += 100

    def operation(_module):
        clock[0] += 1
        return Result(value=1)

    monkeypatch.setattr(benchmark.time, "perf_counter", lambda: clock[0])
    report = benchmark.compare("lifetime", {"fake": object()}, operation, 2, False)
    # Each operation takes 1 simulated second. Its 100-second teardown must
    # happen after the clock reading and before the following sample begins.
    assert report["backends"]["fake"]["seconds"] == [1, 1]
    assert clock[0] == 202
