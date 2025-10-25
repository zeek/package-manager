import os

# For Zeek-bundled installation, make Zeek's binary installation path available
# by default. This helps package installations succeed that require
# e.g. zeek-config for their build process.
ZEEK_BIN_DIR: str | None = "@ZEEK_BIN_DIR@"
assert isinstance(ZEEK_BIN_DIR, str)
if os.path.isdir(ZEEK_BIN_DIR):
    try:
        if ZEEK_BIN_DIR not in os.environ["PATH"].split(os.pathsep):
            os.environ["PATH"] = ZEEK_BIN_DIR + os.pathsep + os.environ["PATH"]
    except KeyError:
        os.environ["PATH"] = ZEEK_BIN_DIR
else:
    ZEEK_BIN_DIR = None

# Also when bundling with Zeek, use directories in the install tree
# for storing the zkg configuration and its variable state. Support
# for overrides via environment variables simplifies testing.
ZEEK_ZKG_CONFIG_DIR: str | None = (
    os.getenv("ZEEK_ZKG_CONFIG_DIR") or "@ZEEK_ZKG_CONFIG_DIR@"
)
assert isinstance(ZEEK_ZKG_CONFIG_DIR, str)
if not os.path.isdir(ZEEK_ZKG_CONFIG_DIR):
    ZEEK_ZKG_CONFIG_DIR = None

ZEEK_ZKG_STATE_DIR: str | None = (
    os.getenv("ZEEK_ZKG_STATE_DIR") or "@ZEEK_ZKG_STATE_DIR@"
)
assert isinstance(ZEEK_ZKG_STATE_DIR, str)
if not os.path.isdir(ZEEK_ZKG_STATE_DIR):
    ZEEK_ZKG_STATE_DIR = None

# The default package source we retrieve from when packages are
# identified by name, not URL:
ZKG_DEFAULT_SOURCE = "https://github.com/zeek/packages"

# The default package template repository:
ZKG_DEFAULT_TEMPLATE = "https://github.com/zeek/package-template"

# The ZKG version:
VERSION = "3.1.0-24"
