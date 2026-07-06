"""This module provides a specialized ConfigParser class to store
zkg configuration settings.
"""

import argparse
import configparser
import os
import sys
from collections import OrderedDict

from ._util import (
    file_is_not_empty,
    std_encoding,
)
from .consts import (
    ZEEK_ZKG_CONFIG_DIR,
    ZEEK_ZKG_STATE_DIR,
    ZKG_DEFAULT_SOURCE,
    ZKG_DEFAULT_TEMPLATE,
)
from .ui import (
    UI,
)


class Config(configparser.ConfigParser):
    def __init__(self) -> None:
        super().__init__()

        # Operate case-sensitively, which helps particularly with user variables.
        # mypy complains about this: Cannot assign to a method  [method-assign]
        Config.optionxform = str  # type: ignore

        # Populate defaults:
        self.reset()

    def reset(self, args: argparse.Namespace | None = None) -> None:
        if args is not None and args.user:
            state_dir = self.home_config_dir()
        else:
            state_dir = self.default_state_dir()

        self.read_dict(
            {
                "paths": {
                    "bin_dir": os.path.join(state_dir, "bin"),
                    "plugin_dir": os.path.join(state_dir, "plugin_dir"),
                    "script_dir": os.path.join(state_dir, "script_dir"),
                    "state_dir": state_dir,
                },
                "sources": {},
                "templates": {},
            },
        )

        # These may turn out to be empty to populate only if present:
        if val := os.getenv("ZKG_DEFAULT_SOURCE", ZKG_DEFAULT_SOURCE):
            self.set("sources", "zeek", val)
        if val := os.getenv("ZKG_DEFAULT_TEMPLATE", ZKG_DEFAULT_TEMPLATE):
            self.set("templates", "default", val)

        self.canonicalize()
        self.sanity_check()

    def save(self, configfile: str) -> bool:
        """Saves the configuration.

        When a filename is given, saves to it; otherwise it uses the default
        config file location logic. Returns True when saving succeeded. May
        throw IOErrors for opening/writing trouble.

        This always saves the config in canonical order, with sections, and keys
        in those sections, ordered alphabetically.
        """
        # dict in Python >= 3.7 is ordered -- use OrderedDict anyway for extra
        # compatibility.
        cfg = configparser.ConfigParser(dict_type=OrderedDict)

        for section in sorted(self.sections()):
            cfg.add_section(section)
            for option in sorted(self.options(section)):
                cfg.set(section, option, self.get(section, option))

        with open(configfile, "w", encoding=std_encoding(sys.stdout)) as hdl:
            cfg.write(hdl)

        return True

    def canonicalize(self) -> None:
        for key, path in self.items("paths"):
            path = os.path.expanduser(path)  # Expand ~
            path = os.path.expandvars(path)  # Env var substitution
            if path:
                # Canonicalize; no trailing slashes -- but only
                # if there is a value, because normpath otherwise
                # produces "."
                path = os.path.normpath(path)
            self.set("paths", key, path)

    def sanity_check(self) -> None:
        # Paths must be absolute:
        for key, val in self.items("paths"):
            if val and not os.path.isabs(val):
                UI.error(
                    "invalid config file value for key"
                    f' "{key}" in section [paths]: "{val}" is not'
                    " an absolute path",
                )
                sys.exit(1)

    def update_from_file(self, configfile: str) -> None:
        if not configfile:
            return
        if not os.path.isfile(configfile):
            UI.error(f'invalid config file "{configfile}"')
            sys.exit(1)

        self.read(configfile)
        self.canonicalize()
        self.sanity_check()

    def update_from_args(self, args: argparse.Namespace) -> None:
        for key_val in args.extra_source or []:
            if "=" not in key_val:
                UI.warning(f'invalid extra source: "{key_val}"')
                continue

            key, val = key_val.split("=", 1)

            if not key or not val:
                UI.warning(f'invalid extra source: "{key_val}"')
                continue

            self.set("sources", key, val)

        self.canonicalize()
        self.sanity_check()

    # def items_without_defaults(section: str) -> list[tuple[str, str]]:
    #     # Same as ConfigParser.items(section), but without default keys.
    #     defaults = {key for key, _ in self.items("DEFAULT")}
    #     items = sorted(self.items(section))
    #     return [(key, val) for (key, val) in items if key not in defaults]

    def set(
        self,
        section: str | configparser._UnnamedSection,  # type: ignore
        option: str,
        val: str | None = None,
    ) -> None:
        """Like ConfigParser's set(), but creating sections on the fly."""
        if not self.has_section(section):
            self.add_section(section)
        super().set(section, option, val)

    def set_interactively(
        self,
        section: str,
        option: str,
        val: str,
        prompt: bool = True,
    ) -> None:
        """Like set(), but with optional prompting.

        When prompting is requested, this will ask the user to confirm new
        options, or altering an already existing config option to a new value.
        """
        if not prompt:
            self.set(section, option, val)
            return

        old_val = self.get(section, option)

        if old_val == val:
            return

        msg = f'Set "{option}" config option to: {val} ?'

        if old_val:
            msg += f"\n(previous value: {old_val})"

        if UI.confirmation_prompt(msg):
            self.set(section, option, val)

    def bin_dir(self) -> str:
        """Directory for package binaries.

        zkg links executables into this directory as provided by an
        installed package through `executables` (as given by its
        :file:`zkg.meta` or :file:`bro-pkg.meta`)

        This is usually $ZEEKROOT/bin.
        """
        return self.get("paths", "bin_dir")

    def script_dir(self) -> str:
        """Toplevel directory for Zeek's site-specific script files.

        This is usually $ZEEKROOT/share/zeek.site.
        """
        return self.get("paths", "script_dir")

    def state_dir(self) -> str:
        """Directory where zkg maintains internal state.

        zkg maintains its manifest file, package/source git clones, and other
        persistent state it requires to operate in this directory.

        This is usually $ZEEKROOT/var/lib.zkg.
        """
        return self.get("paths", "state_dir")

    def plugin_dir(self) -> str:
        """Toplevel directory for Zeek's installed plugins.

        This is usually $ZEEKROOT/lib64/zeek/plugins.
        """
        return self.get("paths", "plugin_dir")

    def zeek_dist(self) -> str:
        """Directory of the Zeek source code distribution, if available.

        The path to the Zeek sources, as per zeek-config. May not be available
        on the system.
        """
        # XXX figure out what to do with this -- it's unusual that people use
        # zkg in combination with an uninstalled, locally built source tree.
        return self.get("paths", "zeek_dist", fallback="")

    # Derived directory paths:

    def backup_dir(self) -> str:
        """Directory for backed-up content.

        zkg will store backup files (e.g. locally modified package config files)
        in this directory.
        """
        return os.path.join(self.state_dir(), "backups")

    def log_dir(self) -> str:
        """Directory where zkg stores its own log files.

        zkg places package build logs, test logs, and similar outputs there.
        """
        return os.path.join(self.state_dir(), "logs")

    def scratch_dir(self) -> str:
        """Directory zkg treats as its own scratch space."""
        return os.path.join(self.state_dir(), "scratch")

    def packages_script_dir(self) -> str:
        """Directory into which to install a package's scripts.

        zkg copies each installed package's `script_dir` (as given by its
        :file:`zkg.meta` or :file:`bro-pkg.meta`).  Each package gets a
        subdirectory associated with its name.
        """
        return os.path.join(self.script_dir(), "packages")

    def packages_plugin_dir(self) -> str:
        """Directory into which to install a package's plugin_dir.

        zkg copies each installed package's `plugin_dir` (as given by its
        :file:`zkg.meta` or :file:`bro-pkg.meta`).  Each package gets a
        subdirectory within `plugin_dir` associated with its name.
        """
        return os.path.join(self.plugin_dir(), "packages")

    def sources_clone_dir(self) -> str:
        """Directory where zkg clones package "source" repos.

        These are zkg's package inventories. Each source gets a subdirectory
        associated with its name. The default source is called "zeek".
        """
        return os.path.join(self.state_dir(), "clones", "sources")

    def packages_clone_dir(self) -> str:
        """Directory where zkg keeps the source tree of installed packages.

        Each package gets a subdirectory associated with its name.
        """
        return os.path.join(self.state_dir(), "clones", "packages")

    def testing_dir(self) -> str:
        """Directory in which zkg runs package tests.

        Each package gets a subdirectory associated with its name.
        """
        return os.path.join(self.state_dir(), "testing")

    def zeek_path(self) -> str:
        """The path where installed package scripts are located.

        This path can be added to :envvar:`ZEEKPATH` for interoperability with
        Zeek.
        """
        return os.path.dirname(self.packages_script_dir())

    def zeek_plugin_path(self) -> str:
        """Return the path where installed package plugins are located.

        This path can be added to :envvar:`ZEEK_PLUGIN_PATH` for
        interoperability with Zeek.
        """
        return os.path.dirname(self.packages_plugin_dir())

    def user_vars(self) -> dict[str, str]:
        """Returns a dictionary of user variables and their values.

        If no user variables are available, returns an empty dictionary.
        """
        res = {}
        if self.has_section("user_vars"):
            for key, val in self.items("user_vars"):
                res[key] = val
        return res

    def home_config_dir(self) -> str:
        """Directory in the user's home in which to store zkg's state.

        This is only used in user mode (--user).
        """
        return os.path.join(os.path.expanduser("~"), ".zkg")

    def default_config_dir(self) -> str:
        """Default directory in which zkg stores its configuration."""
        return ZEEK_ZKG_CONFIG_DIR or self.home_config_dir()

    def default_state_dir(self) -> str:
        """Default directory in which zkg stores all of its state."""
        return ZEEK_ZKG_STATE_DIR or self.home_config_dir()

    def find_configfile(self, args: argparse.Namespace) -> str:
        """Locator for zkg's config file.

        This function attempts the following locations: first, when the provided
        args directly include a config file (`--configfile`), it returns its
        path. Second, if `--user` is given, it attempts to locate the config
        file in the user's home. Third, it attempts the ZKG_CONFIG_FILE
        environment variable, and finally, the default configuration directory.

        Returns the path to the first config file successfully located, and the
        empty string if all attempts fail.
        """
        if args.configfile and file_is_not_empty(args.configfile):
            return str(args.configfile)

        if args.user:
            configfile = os.path.join(self.home_config_dir(), "config")

            if configfile and file_is_not_empty(configfile):
                return configfile

            return ""

        configfile = os.environ.get("ZKG_CONFIG_FILE", default="")

        if configfile and file_is_not_empty(configfile):
            return configfile

        configfile = os.path.join(self.default_config_dir(), "config")

        if file_is_not_empty(configfile):
            return configfile

        return ""


CONFIG = Config()
