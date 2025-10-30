"""Log management for zkg.

This package provides a logger named ``LOG`` to which logging stream handlers
may be added in order to help log/debug zkg's operation.
"""

import logging

LOG: logging.Logger = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())


def configure(verbosity: int = 0) -> None:
    """Configures logging of operational information to stderr.

    Args:
        verbosity (int): the log level. Values 0-2 are supported.
            0 is the default, causing only critical errors to be
            logged. Larger values increase the level of detail.
    """
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    if verbosity == 0:
        LOG.setLevel(logging.WARNING)
    elif verbosity == 1:
        LOG.setLevel(logging.INFO)
    elif verbosity >= 2:
        LOG.setLevel(logging.DEBUG)

    LOG.addHandler(handler)
