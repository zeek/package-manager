"""Log management for zkg."""

import logging

LOG: logging.Logger = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())
