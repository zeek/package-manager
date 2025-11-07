"""
This package defines a Python interface for installing, managing, querying,
and performing other operations on Zeek Packages and Package Sources.
The main entry point is the :class:`Manager <zeekpkg.manager.Manager>` class.
"""

from . import (
    cli,
    config,
    consts,
    logs,
    manager,
    package,
    source,
    template,
    ui,
    uservar,
)

__all__ = [
    "CONFIG",
    "LOG",
    "UI",
    "cli",
    "config",
    "consts",
    "logs",
    "manager",
    "package",
    "source",
    "template",
    "ui",
    "uservar",
]

__version__ = consts.VERSION

from .config import CONFIG
from .logs import LOG
from .ui import UI
