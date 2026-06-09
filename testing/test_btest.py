"""
pytest wrapper for existing BTest suite
"""

import subprocess
from pathlib import Path

import pytest


def btest(*args: str) -> subprocess.CompletedProcess[str]:
    """Runs `btest` with the given arguments."""
    return subprocess.run(
        ["btest", *args],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parent,
    )


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """
    Generates test cases for each discovered btest.

    This looks for parameterized tests taking a `btest_name` argument and
    generates instances for each test case reported by `btest -l`, see
    https://docs.pytest.org/en/stable/how-to/parametrize.html#basic-pytest-generate-tests-example.
    """
    if "btest_name" in metafunc.fixturenames:
        tests = btest("-l").stdout.split()
        metafunc.parametrize("btest_name", tests)


def test_btest(btest_name: str) -> None:
    """pytest wrapper for a btest with the given name."""
    result = btest("-dv", btest_name)

    # Propagate failures from `btest` up into `pytest`.
    if result.returncode != 0:
        error_msg = str(result)
        error_msg += f"stdout:\n{result.stdout}"
        error_msg += f"stderr:\n{result.stderr}"
        pytest.fail(error_msg, False)
