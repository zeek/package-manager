import logging

__version__ = "0.2-1"

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())

from .manager import *
from .error import *
