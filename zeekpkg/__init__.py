"""
This package defines a Python interface for installing, managing, querying,
and performing other operations on Zeek Packages and Package Sources.
The main entry point is the :class:`Manager <zeekpkg.manager.Manager>` class.

This package provides a logger named ``LOG`` to which logging stream handlers
may be added in order to help log/debug applications.
"""

import logging

__version__ = "3.1.0-24"
__all__ = [  # noqa: F405
    "config",
    "consts",
    "manager",
    "package",
    "source",
    "template",
    "uservar",
]

LOG: logging.Logger = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

from .config import *  # noqa: F403
from .consts import *  # noqa: F403
from .manager import *  # noqa: F403
from .package import *  # noqa: F403
from .source import *  # noqa: F403
from .uservar import *  # noqa: F403
