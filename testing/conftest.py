"""Configures pytest runtime behavior for this test suite."""

import os

import pytest

# Point coverage in spawned processes to global coverage configuration and state file.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault(
    "COVERAGE_PROCESS_START",
    os.path.join(_project_root, ".coveragerc"),
)
os.environ.setdefault("COVERAGE_FILE", os.path.join(_project_root, ".coverage"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    # Run slow BTest integration tests last so we get fast feedback from unit tests.
    unit_tests = [i for i in items if i.fspath.basename != "test_btest.py"]
    btest_tests = [i for i in items if i.fspath.basename == "test_btest.py"]
    items[:] = unit_tests + btest_tests
