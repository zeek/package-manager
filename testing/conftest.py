"""Global fixture for setting up coverage for launched subprocesses."""

import os

# Point coverage in spawned processes to global coverage configuration and state file.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault(
    "COVERAGE_PROCESS_START",
    os.path.join(_project_root, ".coveragerc"),
)
os.environ.setdefault("COVERAGE_FILE", os.path.join(_project_root, ".coverage"))
