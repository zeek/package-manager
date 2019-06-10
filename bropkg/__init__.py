from __future__ import print_function
import sys

from zeekpkg import __version__
from zeekpkg import __all__
from zeekpkg import LOG

from zeekpkg.manager import *
from zeekpkg.package import *
from zeekpkg.source import *

print("Warning: the 'bropkg' module is deprecated use 'zeekpkg' instead.",
      file=sys.stderr)
