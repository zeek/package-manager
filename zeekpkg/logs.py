"""Log management for zkg."""

import logging

LOG: logging.Logger = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())


def configure(verbosity: int = 0) -> None:
    """Configures logging.

    Args:
        verbosity (int): the log level. Values 0-3 increase logging, larger
            values make no difference.

        rich_logging (bool): whether to use timestamped, log-style log formatting.
    """
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    if verbosity == 1:
        LOG.setLevel(logging.INFO)
    elif verbosity == 2:
        LOG.setLevel(logging.WARNING)
    elif verbosity >= 3:
        LOG.setLevel(logging.DEBUG)

    LOG.addHandler(handler)
