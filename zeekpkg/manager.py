"""
A module defining the main Zeek Package Manager interface which supplies
methods to interact with and operate on Zeek packages.
"""

import configparser
import copy
import filecmp
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
from collections import deque
from urllib.parse import urlparse

import git
import semantic_version as semver

from . import (
    LOG,
    __version__,
)
from ._util import (
    UmaskContext,
    configparser_section_dict,
    copy_over_path,
    delete_path,
    find_program,
    get_zeek_info,
    get_zeek_version,
    git_checkout,
    git_clone,
    git_default_branch,
    git_pull,
    git_version_tags,
    is_sha1,
    make_dir,
    make_symlink,
    normalize_version_tag,
    read_zeek_config_line,
    std_encoding,
    zkg_tarfile_create,
    zkg_tarfile_extractall,
)
from .package import (
    BUILTIN_SCHEME,
    BUILTIN_SOURCE,
    LEGACY_METADATA_FILENAME,
    LEGACY_PLUGIN_MAGIC_FILE,
    LEGACY_PLUGIN_MAGIC_FILE_DISABLED,
    METADATA_FILENAME,
    PLUGIN_MAGIC_FILE,
    PLUGIN_MAGIC_FILE_DISABLED,
    TRACKING_METHOD_BRANCH,
    TRACKING_METHOD_COMMIT,
    TRACKING_METHOD_VERSION,
    InstalledPackage,
    Package,
    PackageInfo,
    PackageStatus,
    PackageVersion,
    aliases,
    canonical_url,
    make_builtin_package,
    name_from_path,
)
from .package import (
    is_valid_name as is_valid_package_name,
)
from .source import AGGREGATE_DATA_FILE, Source
from .uservar import (
    UserVar,
)


class Stage:
    def __init__(self, manager, state_dir=None):
        self.manager = manager

        if state_dir:
            self.state_dir = state_dir
            self.clone_dir = os.path.join(self.state_dir, "clones")
            self.script_dir = os.path.join(self.state_dir, "scripts", "packages")
            self.plugin_dir = os.path.join(self.state_dir, "plugins", "packages")
            self.bin_dir = os.path.join(self.state_dir, "bin")
        else:
            # Stages not given a test directory are essentially a shortcut to
            # standard functionality; this doesn't require all directories:
            self.state_dir = None
            self.clone_dir = manager.package_clonedir
            self.script_dir = manager.script_dir
            self.plugin_dir = manager.plugin_dir
            self.bin_dir = manager.bin_dir

    def populate(self):
        # If we're staging to a temporary location, blow anything existing there
        # away first.
        if self.state_dir:
            delete_path(self.state_dir)

        make_dir(self.clone_dir)
        make_dir(self.script_dir)
        make_dir(self.plugin_dir)
        make_dir(self.bin_dir)

        # To preserve %(package_base)s functionality in build/test commands
        # during staging in testing folders, we need to provide one location
        # that combines the existing installed packages, plus any under test,
        # with the latter overriding any already installed ones.  We symlink the
        # real install folders into the staging one. The subsequent cloning of
        # the packages under test will remove those links as needed.
        if self.state_dir:
            with os.scandir(self.manager.package_clonedir) as it:
                for entry in it:
                    if not entry.is_dir():
                        continue
                    make_symlink(entry.path, os.path.join(self.clone_dir, entry.name))

    def get_subprocess_env(self):
        zeekpath = os.environ.get("ZEEKPATH")
        pluginpath = os.environ.get("ZEEK_PLUGIN_PATH")

        if not (zeekpath and pluginpath):
            zeek_config = find_program("zeek-config")

            if zeek_config:
                cmd = subprocess.Popen(
                    [zeek_config, "--zeekpath", "--plugin_dir"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                    universal_newlines=True,
                )
                line1 = read_zeek_config_line(cmd.stdout)
                line2 = read_zeek_config_line(cmd.stdout)

                if not zeekpath:
                    zeekpath = line1

                if not pluginpath:
                    pluginpath = line2
            else:
                return None, 'no "zeek-config" found in PATH'

        zeekpath = os.path.dirname(self.script_dir) + os.pathsep + zeekpath
        pluginpath = os.path.dirname(self.plugin_dir) + os.pathsep + pluginpath

        env = os.environ.copy()
        env["PATH"] = self.bin_dir + os.pathsep + os.environ.get("PATH", "")
        env["ZEEKPATH"] = zeekpath
        env["ZEEK_PLUGIN_PATH"] = pluginpath

        return env, ""


class Manager:
    """A package manager object performs various operations on packages.

    It uses a state directory and a manifest file within it to keep
    track of package sources, installed packages and their statuses.

    Attributes:
        sources (dict of str -> :class:`.source.Source`): dictionary package
            sources keyed by the name given to :meth:`add_source()`

        installed_pkgs (dict of str -> :class:`.package.InstalledPackage`):
            a dictionary of installed packaged keyed on package names (the last
            component of the package's git URL)

        zeek_dist (str): path to the Zeek source code distribution.  This
            is needed for packages that contain Zeek plugins that need to be
            built from source code.

        state_dir (str): the directory where the package manager will
            a maintain manifest file, package/source git clones, and other
            persistent state the manager needs in order to operate

        user_vars (dict of str -> str): dictionary of key-value pairs where
            the value will be substituted into package build commands in place
            of the key.

        backup_dir (str): a directory where the package manager will
            store backup files (e.g. locally modified package config files)

        log_dir (str): a directory where the package manager will
            store misc. logs files (e.g. package build logs)

        scratch_dir (str): a directory where the package manager performs
            miscellaneous/temporary file operations

        script_dir (str): the directory where the package manager will
            copy each installed package's `script_dir` (as given by its
            :file:`zkg.meta` or :file:`bro-pkg.meta`).  Each package gets a
            subdirectory within `script_dir` associated with its name.

        plugin_dir (str): the directory where the package manager will
            copy each installed package's `plugin_dir` (as given by its
            :file:`zkg.meta` or :file:`bro-pkg.meta`).  Each package gets a
            subdirectory within `plugin_dir` associated with its name.

        bin_dir (str): the directory where the package manager will link
            executables into that are provided by an installed package through
            `executables` (as given by its :file:`zkg.meta` or
            :file:`bro-pkg.meta`)

        source_clonedir (str): the directory where the package manager
            will clone package sources.  Each source gets a subdirectory
            associated with its name.

        package_clonedir (str): the directory where the package manager
            will clone installed packages.  Each package gets a subdirectory
            associated with its name.

        package_testdir (str): the directory where the package manager
            will run tests.  Each package gets a subdirectory
            associated with its name.

        manifest (str): the path to the package manager's manifest file.
            This file maintains a list of installed packages and their status.

        autoload_script (str): path to a Zeek script named :file:`packages.zeek`
            that the package manager maintains.  It is a list of ``@load`` for
            each installed package that is marked as loaded (see
            :meth:`load()`).

        autoload_package (str): path to a Zeek :file:`__load__.zeek` script
            which is just a symlink to `autoload_script`.  It's always located
            in a directory named :file:`packages`, so as long as
            :envvar:`ZEEKPATH` is configured correctly, ``@load packages`` will
            load all installed packages that have been marked as loaded.
    """

    def __init__(
        self,
        state_dir,
        script_dir,
        plugin_dir,
        zeek_dist="",
        user_vars=None,
        bin_dir="",
    ):
        """Creates a package manager instance.

        Args:
            state_dir (str): value to set the `state_dir` attribute to

            script_dir (str): value to set the `script_dir` attribute to

            plugin_dir (str): value to set the `plugin_dir` attribute to

            zeek_dist (str): value to set the `zeek_dist` attribute to

            user_vars (dict of str -> str): key-value pair substitutions for
                use in package build commands.

            bin_dir (str): value to set the `bin_dir` attribute to.  If
                empty/nil value, defaults to setting `bin_dir` attribute to
                `<state_dir>/bin`.

        Raises:
            OSError: when a package manager state directory can't be created
            IOError: when a package manager state file can't be created
        """
        LOG.debug("init Manager version %s", __version__)
        # TODO: make this umask user-configurable
        self.zkg_umask = 0o022
        self.sources = {}
        self.installed_pkgs = {}
        self._builtin_packages = None  # Cached Zeek built-in packages.
        self._builtin_packages_discovered = False  # Flag if discovery even worked.
        self.zeek_dist = zeek_dist
        self.state_dir = state_dir
        self.user_vars = {} if user_vars is None else user_vars
        self.backup_dir = os.path.join(self.state_dir, "backups")
        self.log_dir = os.path.join(self.state_dir, "logs")
        self.scratch_dir = os.path.join(self.state_dir, "scratch")
        self._script_dir = script_dir
        self.script_dir = os.path.join(script_dir, "packages")
        self._plugin_dir = plugin_dir
        self.plugin_dir = os.path.join(plugin_dir, "packages")
        self.bin_dir = bin_dir or os.path.join(self.state_dir, "bin")
        self.source_clonedir = os.path.join(self.state_dir, "clones", "source")
        self.package_clonedir = os.path.join(self.state_dir, "clones", "package")
        self.package_testdir = os.path.join(self.state_dir, "testing")
        self.manifest = os.path.join(self.state_dir, "manifest.json")
        self.autoload_script = os.path.join(self.script_dir, "packages.zeek")
        self.autoload_package = os.path.join(self.script_dir, "__load__.zeek")
        make_dir(self.state_dir)
        make_dir(self.log_dir)
        make_dir(self.scratch_dir)
        make_dir(self.source_clonedir)
        make_dir(self.package_clonedir)
        make_dir(self.script_dir)
        make_dir(self.plugin_dir)
        make_dir(self.bin_dir)
        _create_readme(os.path.join(self.script_dir, "README"))
        _create_readme(os.path.join(self.plugin_dir, "README"))

        if not os.path.exists(self.manifest):
            self._write_manifest()

        prev_script_dir, prev_plugin_dir, prev_bin_dir = self._read_manifest()

        # Place all Zeek built-in packages into installed packages.
        for info in self.discover_builtin_packages():
            self.installed_pkgs[info.package.name] = InstalledPackage(
                package=info.package,
                status=info.status,
            )

        refresh_bin_dir = False  # whether we need to updates link in bin_dir
        relocating_bin_dir = False  # whether bin_dir has relocated
        need_manifest_update = False

        if os.path.realpath(prev_script_dir) != os.path.realpath(self.script_dir):
            LOG.info("relocating script_dir %s -> %s", prev_script_dir, self.script_dir)

            if os.path.exists(prev_script_dir):
                delete_path(self.script_dir)
                shutil.move(prev_script_dir, self.script_dir)

            prev_zeekpath = os.path.dirname(prev_script_dir)

            for pkg_name in self.installed_pkgs:
                old_link = os.path.join(prev_zeekpath, pkg_name)
                new_link = os.path.join(self.zeekpath(), pkg_name)

                if os.path.lexists(old_link):
                    LOG.info("moving package link %s -> %s", old_link, new_link)
                    shutil.move(old_link, new_link)
                else:
                    LOG.info("skip moving package link %s -> %s", old_link, new_link)

            need_manifest_update = True
            refresh_bin_dir = True

        if os.path.realpath(prev_plugin_dir) != os.path.realpath(self.plugin_dir):
            LOG.info("relocating plugin_dir %s -> %s", prev_plugin_dir, self.plugin_dir)

            if os.path.exists(prev_plugin_dir):
                delete_path(self.plugin_dir)
                shutil.move(prev_plugin_dir, self.plugin_dir)

            need_manifest_update = True
            refresh_bin_dir = True

        if prev_bin_dir and os.path.realpath(prev_bin_dir) != os.path.realpath(
            self.bin_dir,
        ):
            LOG.info("relocating bin_dir %s -> %s", prev_bin_dir, self.bin_dir)
            need_manifest_update = True
            refresh_bin_dir = True
            relocating_bin_dir = True

        if refresh_bin_dir:
            self._refresh_bin_dir(self.bin_dir)

        if relocating_bin_dir:
            self._clear_bin_dir(prev_bin_dir)
            try:
                # We try to remove the old bin_dir. That may not succeed in case
                # it wasn't actually managed by us, but that's ok.
                os.rmdir(prev_bin_dir)
            except OSError:
                pass

        if need_manifest_update:
            self._write_manifest()

        self._write_autoloader()
        make_symlink("packages.zeek", self.autoload_package)

    def _write_autoloader(self):
        """Write the :file:`packages.zeek` loader script.

        Raises:
            IOError: if :file:`packages.zeek` loader script cannot be written
        """
        with open(self.autoload_script, "w") as f:
            content = (
                "# WARNING: This file is managed by zkg.\n"
                "# Do not make direct modifications here.\n"
            )

            for ipkg in self.loaded_packages():
                if self.has_scripts(ipkg):
                    content += f"@load ./{ipkg.package.name}\n"

            f.write(content)

    def _write_plugin_magic(self, ipkg):
        """Enables/disables any Zeek plugin included with a package.

        Zeek's plugin code scans its plugin directories for
        __zeek_plugin__ magic files, which indicate presence of a
        plugin directory. When this file does not exist, Zeek does not
        recognize a plugin.

        When we're loading a package, this function renames an
        existing __zeek_plugin__.disabled file to __zeek_plugin__, and
        vice versa when we're unloading a package.

        When the package doesn't include a plugin, or when the plugin
        directory already contains a correctly named magic file, this
        function does nothing.

        Until Zeek 6.1, the magic file was named __bro_plugin__. zkg implements
        a fallback for recognizing the older name so that newer zkg versions
        continue to work with older Zeek versions for some time longer.
        """
        package_dir = pathlib.Path(self.plugin_dir) / ipkg.package.name

        magic_paths_enabled = [
            package_dir / PLUGIN_MAGIC_FILE,
            package_dir / LEGACY_PLUGIN_MAGIC_FILE,
        ]

        magic_paths_disabled = [
            package_dir / PLUGIN_MAGIC_FILE_DISABLED,
            package_dir / LEGACY_PLUGIN_MAGIC_FILE_DISABLED,
        ]

        for path_enabled, path_disabled in zip(
            magic_paths_enabled,
            magic_paths_disabled,
        ):
            if ipkg.status.is_loaded:
                if path_disabled.exists():
                    try:
                        path_disabled.rename(path_enabled)
                    except OSError as exception:
                        LOG.error(
                            "could not enable plugin: %s %s",
                            type(exception).__name__,
                            exception,
                        )
            else:
                if path_enabled.exists():
                    try:
                        path_enabled.rename(path_disabled)
                    except OSError as exception:
                        LOG.error(
                            "could not disable plugin: %s %s",
                            type(exception).__name__,
                            exception,
                        )

    def _read_manifest(self):
        """Read the manifest file containing the list of installed packages.

        Returns:
            tuple: (previous script_dir, previous plugin_dir)

        Raises:
            IOError: when the manifest file can't be read
        """
        with open(self.manifest) as f:
            data = json.load(f)
            version = data["manifest_version"]
            pkg_list = data["installed_packages"]
            self.installed_pkgs = {}

            for dicts in pkg_list:
                pkg_dict = dicts["package_dict"]
                status_dict = dicts["status_dict"]
                pkg_name = pkg_dict["name"]

                if version == 0 and "index_data" in pkg_dict:
                    del pkg_dict["index_data"]

                pkg_dict["canonical"] = True
                pkg = Package(**pkg_dict)
                status = PackageStatus(**status_dict)
                self.installed_pkgs[pkg_name] = InstalledPackage(pkg, status)

            return data["script_dir"], data["plugin_dir"], data.get("bin_dir", None)

    def _write_manifest(self):
        """Writes the manifest file containing the list of installed packages.

        Raises:
            IOError: when the manifest file can't be written
        """
        pkg_list = []

        for _, installed_pkg in self.installed_pkgs.items():
            if installed_pkg.is_builtin():
                continue

            pkg_list.append(
                {
                    "package_dict": installed_pkg.package.__dict__,
                    "status_dict": installed_pkg.status.__dict__,
                },
            )

        data = {
            "manifest_version": 1,
            "script_dir": self.script_dir,
            "plugin_dir": self.plugin_dir,
            "bin_dir": self.bin_dir,
            "installed_packages": pkg_list,
        }

        with open(self.manifest, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)

    def zeekpath(self):
        """Return the path where installed package scripts are located.

        This path can be added to :envvar:`ZEEKPATH` for interoperability with
        Zeek.
        """
        return os.path.dirname(self.script_dir)

    def zeek_plugin_path(self):
        """Return the path where installed package plugins are located.

        This path can be added to :envvar:`ZEEK_PLUGIN_PATH` for
        interoperability with Zeek.
        """
        return os.path.dirname(self.plugin_dir)

    def add_source(self, name, git_url):
        """Add a git repository that acts as a source of packages.

        Args:
            name (str): a short name that will be used to reference the package
                source.

            git_url (str): the git URL of the package source

        Returns:
            str: empty string if the source is successfully added, else the
            reason why it failed.
        """
        if name == BUILTIN_SOURCE:
            return f"{name} is a reserved source name"

        if name in self.sources:
            existing_source = self.sources[name]

            if existing_source.git_url == git_url:
                LOG.debug('duplicate source "%s"', name)
                return True

            return (
                f"source already exists with different URL: {existing_source.git_url}"
            )

        clone_path = os.path.join(self.source_clonedir, name)

        # Support @ in the path to denote the "version" to checkout
        version = None

        # Prepend 'ssh://' and replace the first ':' with '/' if git_url
        # looks like a scp-like URL, e.g. git@github.com:user/repo.git.
        # urlparse will otherwise parse everything into path and the @
        # is confusing the versioning logic. Note that per the git-clone
        # docs git recognizes scp-style URLs only when there are no slashes
        # before the first colon.
        colonidx, slashidx = git_url.find(":"), git_url.find("/")

        if (
            "://" not in git_url
            and colonidx > 0
            and (slashidx == -1 or slashidx > colonidx)
        ):
            parse_result = urlparse("ssh://" + git_url.replace(":", "/", 1))
        else:
            parse_result = urlparse(git_url)

        if parse_result.path and "@" in parse_result.path:
            git_url, version = git_url.rsplit("@", 1)

        try:
            source = Source(
                name=name,
                clone_path=clone_path,
                git_url=git_url,
                version=version,
            )
        except git.GitCommandError as error:
            LOG.warning("failed to clone git repo: %s", error)
            return "failed to clone git repo"
        else:
            self.sources[name] = source

        return ""

    def source_packages(self):
        """Return a list of :class:`.package.Package` within all sources."""
        rval = []

        for _, source in self.sources.items():
            rval += source.packages()

        return rval

    def discover_builtin_packages(self):
        """
        Discover packages included in Zeek for dependency resolution.

        This is using Zeek's ``--build-info`` flag and specifically the
        ``zkg.provides`` entry it contains. Requires Zeek 6.0 and later.

        Returns:
            list of :class:`.package.BuiltinPackage`: List of built-in packages.
        """
        if self._builtin_packages is not None:
            return self._builtin_packages

        self._builtin_packages = []
        try:
            zeek_executable = get_zeek_info().zeek
        except LookupError as e:
            LOG.warning("unable to discover builtin-packages: %s", str(e))
            return self._builtin_packages

        try:
            build_info_str = subprocess.check_output(
                [zeek_executable, "--build-info"],
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            build_info = json.loads(build_info_str)
        except subprocess.CalledProcessError:
            # Not a warning() due to being a bit noisy.
            LOG.info("unable to discover built-in packages - requires Zeek 6.0")
            return self._builtin_packages
        except json.JSONDecodeError as e:
            LOG.error("unable to parse Zeek's build info output: %s", str(e))
            return self._builtin_packages

        if "zkg" not in build_info or "provides" not in build_info["zkg"]:
            LOG.warning("missing zkg.provides entry in zeek --build-info output")
            return self._builtin_packages

        self._builtin_packages_discovered = True

        for p in build_info["zkg"]["provides"]:
            name, version = p.get("name"), p.get("version")
            commit = p.get("commit")

            if not name or not version:
                LOG.warning("zkg.provides entry missing name or version: %s", repr(p))
                continue

            orig_version = version
            # The "version" field may not be semantic version compatible.
            # For example, 1.4.2-68 is parsed as prerelease 68 of 1.4.2, but
            # from update-changes/git describe, it's 68 commits after 1.4.2.
            # Deal with that by stripping -68, but leave -rc1 or -dev alone.
            m = re.match(r"([0-9]+\.[0-9]+\.[0-9]+)-[0-9]+", version)
            if m:
                version = m.group(1)

            LOG.debug(
                "found built-in package %s with version %s (%s)",
                name,
                version,
                orig_version,
            )

            self._builtin_packages.append(
                make_builtin_package(
                    name=name,
                    current_version=version,
                    current_hash=commit,
                ),
            )

        return self._builtin_packages

    def find_builtin_package(self, pkg_path):
        """
        Find a builtin plugin that matches ``pkg_path``.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.

        Returns:
            PackageInfo: PackageInfo instance representing a builtin package
            matching ``pkg_path``.
        """
        pkg_name = name_from_path(pkg_path)
        for info in self.discover_builtin_packages():
            if info.package.matches_path(pkg_name):
                return info

        return None

    def installed_packages(self):
        """Return list of :class:`.package.InstalledPackage`."""
        return [ipkg for _, ipkg in sorted(self.installed_pkgs.items())]

    def installed_package_dependencies(self):
        """Return dict of 'package' -> dict of 'dependency' -> 'version'.

        Package-name / dependency-name / and version-requirement values are
        all strings.
        """
        return {
            name: ipkg.package.dependencies()
            for name, ipkg in self.installed_pkgs.items()
        }

    def loaded_packages(self):
        """Return list of loaded :class:`.package.InstalledPackage`."""
        rval = []

        for _, ipkg in sorted(self.installed_pkgs.items()):
            if ipkg.status.is_loaded:
                rval.append(ipkg)

        return rval

    def package_build_log(self, pkg_path):
        """Return the path to the package manager's build log for a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".
        """
        name = name_from_path(pkg_path)
        return os.path.join(self.log_dir, f"{name}-build.log")

    def match_source_packages(self, pkg_path):
        """Return a list of :class:`.package.Package` that match a given path.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".
        """
        rval = []
        canon_url = canonical_url(pkg_path)

        for pkg in self.source_packages():
            if pkg.matches_path(canon_url):
                rval.append(pkg)

        return rval

    def find_installed_package(self, pkg_path):
        """Return an :class:`.package.InstalledPackage` if one matches the name.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".
        """
        pkg_name = name_from_path(pkg_path)
        return self.installed_pkgs.get(pkg_name)

    def get_installed_package_dependencies(self, pkg_path):
        """Return a set of tuples of dependent package names and their version
        number if pkg_path is an installed package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".
        """
        ipkg = self.find_installed_package(pkg_path)

        if ipkg:
            return ipkg.package.dependencies()

        return None

    def has_scripts(self, installed_pkg):
        """Return whether a :class:`.package.InstalledPackage` installed scripts.

        Args:
            installed_pkg(:class:`.package.InstalledPackage`): the installed
                package to check for whether it has installed any Zeek scripts.

        Returns:
            bool: True if the package has installed Zeek scripts.
        """
        return os.path.exists(os.path.join(self.script_dir, installed_pkg.package.name))

    def has_plugin(self, installed_pkg):
        """Return whether a :class:`.package.InstalledPackage` installed a plugin.

        Args:
            installed_pkg(:class:`.package.InstalledPackage`): the installed
                package to check for whether it has installed a Zeek plugin.

        Returns:
            bool: True if the package has installed a Zeek plugin.
        """
        return os.path.exists(os.path.join(self.plugin_dir, installed_pkg.package.name))

    def save_temporary_config_files(self, installed_pkg):
        """Return a list of temporary package config file backups.

        Args:
            installed_pkg(:class:`.package.InstalledPackage`): the installed
                package to save temporary config file backups for.

        Returns:
            list of (str, str): tuples that describe the config files backups.
            The first element is the config file as specified in the package
            metadata (a file path relative to the package's root directory).
            The second element is an absolute file system path to where that
            config file has been copied.  It should be considered temporary,
            so make use of it before doing any further operations on packages.
        """
        import re

        metadata = installed_pkg.package.metadata
        config_files = re.split(r",\s*", metadata.get("config_files", ""))

        if not config_files:
            return []

        pkg_name = installed_pkg.package.name
        clone_dir = os.path.join(self.package_clonedir, pkg_name)
        rval = []

        for config_file in config_files:
            config_file_path = os.path.join(clone_dir, config_file)

            if not os.path.isfile(config_file_path):
                LOG.info(
                    "package '%s' claims config file at '%s', but it does not exist",
                    pkg_name,
                    config_file,
                )
                continue

            backup_file = os.path.join(self.scratch_dir, "tmpcfg", config_file)
            make_dir(os.path.dirname(backup_file))
            shutil.copy2(config_file_path, backup_file)
            rval.append((config_file, backup_file))

        return rval

    def modified_config_files(self, installed_pkg):
        """Return a list of package config files that the user has modified.

        Args:
            installed_pkg(:class:`.package.InstalledPackage`): the installed
                package to check for whether it has installed any Zeek scripts.

        Returns:
            list of (str, str): tuples that describe the modified config files.
            The first element is the config file as specified in the package
            metadata (a file path relative to the package's root directory).
            The second element is an absolute file system path to where that
            config file is currently installed.
        """
        import re

        metadata = installed_pkg.package.metadata
        config_files = re.split(r",\s*", metadata.get("config_files", ""))

        if not config_files:
            return []

        pkg_name = installed_pkg.package.name
        script_install_dir = os.path.join(self.script_dir, pkg_name)
        plugin_install_dir = os.path.join(self.plugin_dir, pkg_name)
        clone_dir = os.path.join(self.package_clonedir, pkg_name)
        script_dir = metadata.get("script_dir", "")
        plugin_dir = metadata.get("plugin_dir", "build")
        rval = []

        for config_file in config_files:
            their_config_file_path = os.path.join(clone_dir, config_file)

            if not os.path.isfile(their_config_file_path):
                LOG.info(
                    "package '%s' claims config file at '%s', but it does not exist",
                    pkg_name,
                    config_file,
                )
                continue

            if config_file.startswith(plugin_dir):
                our_config_file_path = os.path.join(
                    plugin_install_dir,
                    config_file[len(plugin_dir) :],
                )

                if not os.path.isfile(our_config_file_path):
                    LOG.info(
                        "package '%s' config file '%s' not found in plugin_dir: %s",
                        pkg_name,
                        config_file,
                        our_config_file_path,
                    )
                    continue
            elif config_file.startswith(script_dir):
                our_config_file_path = os.path.join(
                    script_install_dir,
                    config_file[len(script_dir) :],
                )

                if not os.path.isfile(our_config_file_path):
                    LOG.info(
                        "package '%s' config file '%s' not found in script_dir: %s",
                        pkg_name,
                        config_file,
                        our_config_file_path,
                    )
                    continue
            else:
                # Their config file is outside script/plugin install dirs,
                # so no way user has it even installed, much less modified.
                LOG.warning(
                    "package '%s' config file '%s' not within plugin_dir or script_dir",
                    pkg_name,
                    config_file,
                )
                continue

            if not filecmp.cmp(our_config_file_path, their_config_file_path):
                rval.append((config_file, our_config_file_path))

        return rval

    def backup_modified_files(self, backup_subdir, modified_files):
        """Creates backups of modified config files

        Args:
            modified_files(list of (str, str)): the return value of
                :meth:`modified_config_files()`.

            backup_subdir(str): the subdir of `backup_dir` in which

        Returns:
            list of str: paths indicating the backup locations.  The order
            of the returned list corresponds directly to the order of
            `modified_files`.
        """
        import time

        rval = []

        for modified_file in modified_files:
            config_file = modified_file[0]
            config_file_dir = os.path.dirname(config_file)
            install_path = modified_file[1]
            filename = os.path.basename(install_path)
            backup_dir = os.path.join(self.backup_dir, backup_subdir, config_file_dir)
            timestamp = time.strftime(".%Y-%m-%d-%H:%M:%S")
            backup_path = os.path.join(backup_dir, filename + timestamp)
            make_dir(backup_dir)
            shutil.copy2(install_path, backup_path)
            rval.append(backup_path)

        return rval

    class SourceAggregationResults:
        """The return value of a call to :meth:`.Manager.aggregate_source()`.

        Attributes:
            refresh_error (str): an empty string if no overall error
                occurred in the "refresh" operation, else a description of
                what wrong

            package_issues (list of (str, str)): a list of reasons for
                failing to collect metadata per packages/repository.
                The first tuple element gives the repository URL in which
                the problem occurred and the second tuple element describes
                the failure.
        """

        def __init__(self, refresh_error="", package_issues=None):
            self.refresh_error = refresh_error
            self.package_issues = package_issues if package_issues else []

    def aggregate_source(self, name, push=False):
        """Pull latest git info from a package source and aggregate metadata.

        This is like calling :meth:`refresh_source()` with the *aggregate*
        arguments set to True.

        This makes the latest pre-aggregated package metadata available or
        performs the aggregation locally in order to push it to the actual
        package source.  Locally aggregated data also takes precedence over
        the source's pre-aggregated data, so it can be useful in the case
        the operator of the source does not update their pre-aggregated data
        at a frequent enough interval.

        Args:
            name(str): the name of the package source.  E.g. the same name
                used as a key to :meth:`add_source()`.

            push (bool): whether to push local changes to the aggregated
                metadata to the remote package source.

        Returns:
            :class:`.Manager.SourceAggregationResults`: the results of the
                refresh/aggregation.
        """
        return self._refresh_source(name, True, push)

    def refresh_source(self, name, aggregate=False, push=False):
        """Pull latest git information from a package source.

        This makes the latest pre-aggregated package metadata available or
        performs the aggregation locally in order to push it to the actual
        package source.  Locally aggregated data also takes precedence over
        the source's pre-aggregated data, so it can be useful in the case
        the operator of the source does not update their pre-aggregated data
        at a frequent enough interval.

        Args:
            name(str): the name of the package source.  E.g. the same name
                used as a key to :meth:`add_source()`.

            aggregate (bool): whether to perform a local metadata aggregation
                by crawling all packages listed in the source's index files.

            push (bool): whether to push local changes to the aggregated
                metadata to the remote package source.  If the `aggregate`
                flag is set, the data will be pushed after the aggregation
                is finished.

        Returns:
            str: an empty string if no errors occurred, else a description
            of what went wrong.
        """
        res = self._refresh_source(name, aggregate, push)
        return res.refresh_error

    def _refresh_source(self, name, aggregate=False, push=False):
        """Used by :meth:`refresh_source()` and :meth:`aggregate_source()`."""
        if name not in self.sources:
            return self.SourceAggregationResults("source name does not exist")

        source = self.sources[name]
        LOG.debug('refresh "%s": pulling %s', name, source.git_url)
        aggregate_file = os.path.join(source.clone.working_dir, AGGREGATE_DATA_FILE)
        agg_file_ours = os.path.join(self.scratch_dir, AGGREGATE_DATA_FILE)
        agg_file_their_orig = os.path.join(
            self.scratch_dir,
            AGGREGATE_DATA_FILE + ".orig",
        )

        delete_path(agg_file_ours)
        delete_path(agg_file_their_orig)

        if os.path.isfile(aggregate_file):
            shutil.copy2(aggregate_file, agg_file_ours)

        source.clone.git.reset(hard=True)
        source.clone.git.clean("-f", "-x", "-d")

        if os.path.isfile(aggregate_file):
            shutil.copy2(aggregate_file, agg_file_their_orig)

        try:
            source.clone.git.fetch("--recurse-submodules=yes")
            git_pull(source.clone)
        except git.GitCommandError as error:
            LOG.error("failed to pull source %s: %s", name, error)
            return self.SourceAggregationResults(
                f"failed to pull from remote source: {error}",
            )

        if os.path.isfile(agg_file_ours):
            if os.path.isfile(aggregate_file):
                # There's a tracked version of the file after pull.
                if os.path.isfile(agg_file_their_orig):
                    # We had local modifications to the file.
                    if filecmp.cmp(aggregate_file, agg_file_their_orig):
                        # Their file hasn't changed, use ours.
                        shutil.copy2(agg_file_ours, aggregate_file)
                        LOG.debug(
                            "aggegrate file in source unchanged, restore local one",
                        )
                    else:
                        # Their file changed, use theirs.
                        LOG.debug("aggegrate file in source changed, discard local one")
                else:
                    # File was untracked before pull and tracked after,
                    # use their version.
                    LOG.debug("new aggegrate file in source, discard local one")
            else:
                # They don't have the file after pulling, so restore ours.
                shutil.copy2(agg_file_ours, aggregate_file)
                LOG.debug("no aggegrate file in source, restore local one")

        aggregation_issues = []

        if aggregate:
            parser = configparser.ConfigParser(interpolation=None)
            prev_parser = configparser.ConfigParser(interpolation=None)
            prev_packages = set()

            if os.path.isfile(aggregate_file):
                prev_parser.read(aggregate_file)
                prev_packages = set(prev_parser.sections())

            agg_adds = []
            agg_mods = []
            agg_dels = []

            for index_file in source.package_index_files():
                urls = []

                with open(index_file) as f:
                    urls = [line.rstrip("\n") for line in f]

                for url in urls:
                    pkg_name = name_from_path(url)
                    clonepath = os.path.join(self.scratch_dir, pkg_name)
                    delete_path(clonepath)

                    try:
                        clone = git_clone(url, clonepath, shallow=True)
                    except git.GitCommandError as error:
                        LOG.warn(
                            "failed to clone %s, skipping aggregation: %s",
                            url,
                            error,
                        )
                        aggregation_issues.append((url, repr(error)))
                        continue

                    version_tags = git_version_tags(clone)

                    if len(version_tags):
                        version = version_tags[-1]
                    else:
                        version = git_default_branch(clone)

                    try:
                        git_checkout(clone, version)
                    except git.GitCommandError as error:
                        LOG.warn(
                            'failed to checkout branch/version "%s" of %s, '
                            "skipping aggregation: %s",
                            version,
                            url,
                            error,
                        )
                        msg = (
                            f'failed to checkout branch/version "{version}": {error!r}'
                        )
                        aggregation_issues.append((url, msg))
                        continue

                    metadata_file = _pick_metadata_file(clone.working_dir)
                    metadata_parser = configparser.ConfigParser(interpolation=None)
                    invalid_reason = _parse_package_metadata(
                        metadata_parser,
                        metadata_file,
                    )

                    if invalid_reason:
                        LOG.warn(
                            "skipping aggregation of %s: bad metadata: %s",
                            url,
                            invalid_reason,
                        )
                        aggregation_issues.append((url, invalid_reason))
                        continue

                    metadata = _get_package_metadata(metadata_parser)
                    index_dir = os.path.dirname(index_file)[
                        len(self.source_clonedir) + len(name) + 2 :
                    ]
                    qualified_name = os.path.join(index_dir, pkg_name)

                    parser.add_section(qualified_name)

                    for key, value in sorted(metadata.items()):
                        parser.set(qualified_name, key, value)

                    parser.set(qualified_name, "url", url)
                    parser.set(qualified_name, "version", version)

                    if qualified_name not in prev_packages:
                        agg_adds.append(qualified_name)
                    else:
                        prev_meta = configparser_section_dict(
                            prev_parser,
                            qualified_name,
                        )
                        new_meta = configparser_section_dict(parser, qualified_name)
                        if prev_meta != new_meta:
                            agg_mods.append(qualified_name)

            with open(aggregate_file, "w") as f:
                parser.write(f)

            agg_dels = list(prev_packages.difference(set(parser.sections())))

            adds_str = " (" + ", ".join(sorted(agg_adds)) + ")" if agg_adds else ""
            mods_str = " (" + ", ".join(sorted(agg_mods)) + ")" if agg_mods else ""
            dels_str = " (" + ", ".join(sorted(agg_dels)) + ")" if agg_dels else ""

            LOG.debug(
                "metadata refresh: %d additions%s, %d changes%s, %d removals%s",
                len(agg_adds),
                adds_str,
                len(agg_mods),
                mods_str,
                len(agg_dels),
                dels_str,
            )

        if push:
            if os.path.isfile(
                os.path.join(source.clone.working_dir, AGGREGATE_DATA_FILE),
            ):
                source.clone.git.add(AGGREGATE_DATA_FILE)

            if source.clone.is_dirty():
                # There's an assumption here that the dirty state is
                # due to a metadata refresh. This could be incorrect
                # if somebody makes local modifications and then runs
                # the refresh without --aggregate, but it's not clear
                # why one would use zkg for this as opposed to git
                # itself.
                source.clone.git.commit(
                    "--no-verify",
                    "--message",
                    "Update aggregated metadata.",
                )
                LOG.info('committed package source "%s" metadata update', name)

            source.clone.git.push("--no-verify")

        return self.SourceAggregationResults("", aggregation_issues)

    def refresh_installed_packages(self):
        """Fetch latest git information for installed packages.

        This retrieves information about outdated packages, but does
        not actually upgrade their installations.

        Raises:
            IOError: if the package manifest file can't be written
        """
        for ipkg in self.installed_packages():
            if ipkg.is_builtin():
                LOG.debug(
                    'skipping refresh of built-in package "%s"',
                    ipkg.package.name,
                )
                continue

            clonepath = os.path.join(self.package_clonedir, ipkg.package.name)
            clone = git.Repo(clonepath)
            LOG.debug("fetch package %s", ipkg.package.qualified_name())

            try:
                clone.git.fetch("--recurse-submodules=yes")
            except git.GitCommandError as error:
                LOG.warn(
                    "failed to fetch package %s: %s",
                    ipkg.package.qualified_name(),
                    error,
                )

            ipkg.status.is_outdated = _is_clone_outdated(
                clone,
                ipkg.status.current_version,
                ipkg.status.tracking_method,
            )

        self._write_manifest()

    def upgrade(self, pkg_path):
        """Upgrade a package to the latest available version.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            str: an empty string if package upgrade succeeded else an error
            string explaining why it failed.

        Raises:
            IOError: if the manifest can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('upgrading "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('upgrading "%s": no matching package', pkg_path)
            return "no such package installed"

        if ipkg.status.is_pinned:
            LOG.info('upgrading "%s": package is pinned', pkg_path)
            return "package is pinned"

        if not ipkg.status.is_outdated:
            LOG.info('upgrading "%s": package not outdated', pkg_path)
            return "package is not outdated"

        clonepath = os.path.join(self.package_clonedir, ipkg.package.name)
        clone = git.Repo(clonepath)

        if ipkg.status.tracking_method == TRACKING_METHOD_VERSION:
            version_tags = git_version_tags(clone)
            return self._install(ipkg.package, version_tags[-1])

        if ipkg.status.tracking_method == TRACKING_METHOD_BRANCH:
            git_pull(clone)
            return self._install(ipkg.package, ipkg.status.current_version)

        if ipkg.status.tracking_method == TRACKING_METHOD_COMMIT:
            # The above check for whether the installed package is outdated
            # also should have already caught this situation.
            return "package is not outdated"

        raise NotImplementedError

    def remove(self, pkg_path):
        """Remove an installed package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            bool: True if an installed package was removed, else False.

        Raises:
            IOError: if the package manifest file can't be written
            OSError: if the installed package's directory can't be deleted
        """

        pkg_path = canonical_url(pkg_path)
        LOG.debug('removing "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('removing "%s": could not find matching package', pkg_path)
            return False

        if ipkg.is_builtin():
            LOG.error('cannot remove built-in package "%s"', pkg_path)
            return False

        self.unload(pkg_path)

        pkg_to_remove = ipkg.package
        delete_path(os.path.join(self.package_clonedir, pkg_to_remove.name))
        delete_path(os.path.join(self.script_dir, pkg_to_remove.name))
        delete_path(os.path.join(self.plugin_dir, pkg_to_remove.name))
        delete_path(os.path.join(self.zeekpath(), pkg_to_remove.name))

        for alias in pkg_to_remove.aliases():
            delete_path(os.path.join(self.zeekpath(), alias))

        for exe in self._get_executables(pkg_to_remove.metadata):
            link = os.path.join(self.bin_dir, os.path.basename(exe))
            if os.path.islink(link):
                try:
                    LOG.debug("removing link %s", link)
                    os.unlink(link)
                except OSError as err:
                    LOG.warn("cannot remove link for %s", err)

        del self.installed_pkgs[pkg_to_remove.name]
        self._write_manifest()

        LOG.debug('removed "%s"', pkg_path)
        return True

    def pin(self, pkg_path):
        """Pin a currently installed package to the currently installed version.

        Pinned packages are never upgraded when calling :meth:`upgrade()`.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            :class:`.package.InstalledPackage`: None if no matching installed
            package could be found, else the installed package that was pinned.

        Raises:
            IOError: when the manifest file can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('pinning "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('pinning "%s": no matching package', pkg_path)
            return None

        if ipkg.status.is_pinned:
            LOG.debug('pinning "%s": already pinned', pkg_path)
            return ipkg

        ipkg.status.is_pinned = True
        self._write_manifest()
        LOG.debug('pinned "%s"', pkg_path)
        return ipkg

    def unpin(self, pkg_path):
        """Unpin a currently installed package and allow it to be upgraded.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            :class:`.package.InstalledPackage`: None if no matching installed
            package could be found, else the installed package that was unpinned.

        Raises:
            IOError: when the manifest file can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('unpinning "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('unpinning "%s": no matching package', pkg_path)
            return None

        if not ipkg.status.is_pinned:
            LOG.debug('unpinning "%s": already unpinned', pkg_path)
            return ipkg

        ipkg.status.is_pinned = False
        self._write_manifest()
        LOG.debug('unpinned "%s"', pkg_path)
        return ipkg

    def load(self, pkg_path):
        """Mark an installed package as being "loaded".

        The collection of "loaded" packages is a convenient way for Zeek to more
        simply load a whole group of packages installed via the package manager.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            str: empty string if the package is successfully marked as loaded,
            else an explanation of why it failed.

        Raises:
            IOError: if the loader script or manifest can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('loading "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('loading "%s": no matching package', pkg_path)
            return "no such package"

        if ipkg.status.is_loaded:
            LOG.debug('loading "%s": already loaded', pkg_path)
            return ""

        pkg_load_script = os.path.join(
            self.script_dir,
            ipkg.package.name,
            "__load__.zeek",
        )

        if not os.path.exists(pkg_load_script) and not self.has_plugin(ipkg):
            LOG.debug(
                'loading "%s": %s not found and package has no plugin',
                pkg_path,
                pkg_load_script,
            )
            return "no __load__.zeek within package script_dir and no plugin included"

        ipkg.status.is_loaded = True
        self._write_autoloader()
        self._write_manifest()
        self._write_plugin_magic(ipkg)
        LOG.debug('loaded "%s"', pkg_path)
        return ""

    def loaded_package_states(self):
        """Save "loaded" state for all installed packages.

        Returns:
            dict: dictionary of "loaded" status for installed packages
        """
        return {
            name: ipkg.status.is_loaded for name, ipkg in self.installed_pkgs.items()
        }

    def restore_loaded_package_states(self, saved_state):
        """Restores state for installed packages.

        Args:
            saved_state (dict): dictionary of saved "loaded" state for installed
            packages.

        """
        for pkg_name, ipkg in self.installed_pkgs.items():
            if ipkg.status.is_loaded == saved_state[pkg_name]:
                continue

            ipkg.status.is_loaded = saved_state[pkg_name]
            self._write_plugin_magic(ipkg)

        self._write_autoloader()
        self._write_manifest()

    def load_with_dependencies(self, pkg_name, visited=None):
        """Mark dependent (but previously installed) packages as being "loaded".

        Args:
            pkg_name (str): name of the package.

            visited (set(str)): set of packages visited along the recursive loading

        Returns:
            list(str, str): list of tuples containing dependent package name and whether
            it was marked as loaded or else an explanation of why the loading failed.

        """
        if visited is None:
            visited = set()

        ipkg = self.find_installed_package(pkg_name)

        # skip loading a package if it is not installed.
        if not ipkg:
            return [(pkg_name, "Loading dependency failed. Package not installed.")]

        load_error = self.load(pkg_name)

        if load_error:
            return [(pkg_name, load_error)]

        retval = []
        visited.add(pkg_name)

        for pkg in self.get_installed_package_dependencies(pkg_name):
            if _is_reserved_pkg_name(pkg):
                continue

            if pkg in visited:
                continue

            retval += self.load_with_dependencies(pkg, visited)

        return retval

    def list_depender_pkgs(self, pkg_path):
        """List of depender packages.

        If C depends on B and B depends on A, we represent the dependency
        chain as C -> B -> A. Thus, package C is dependent on A and B,
        while package B is dependent on just A.  Example representation::

            {
            'A': set(),
            'B': set([A, version_of_A])
            'C': set([B, version_of_B])
            }

        Further, package A is a direct dependee for B (and implicitly for C),
        while B is a direct depender (and C is an implicit depender) for A.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            list: list of depender packages.
        """
        depender_packages, pkg_name = set(), name_from_path(pkg_path)
        queue = deque([pkg_name])
        pkg_dependencies = self.installed_package_dependencies()

        while queue:
            item = queue.popleft()

            for _pkg_name in pkg_dependencies:
                pkg_dependees = set(pkg_dependencies.get(_pkg_name))

                if item in pkg_dependees:
                    # check if there is a cyclic dependency
                    if _pkg_name == pkg_name:
                        return sorted([*list(depender_packages), [pkg_name]])

                    queue.append(_pkg_name)
                    depender_packages.add(_pkg_name)

        return sorted(depender_packages)

    def unload_with_unused_dependers(self, pkg_name):
        """Unmark dependent (but previously installed packages) as being "loaded".

        Args:
            pkg_name (str): name of the package.

        Returns:
            list(str, str): list of tuples containing dependent package name and
            whether it was marked as unloaded or else an explanation of why the
            unloading failed.

        Raises:
            IOError: if the loader script or manifest can't be written
        """

        def _has_all_dependers_unloaded(item, dependers):
            for depender in dependers:
                ipkg = self.find_installed_package(depender)
                if ipkg and ipkg.status.is_loaded:
                    return False
            return True

        errors = []
        queue = deque([pkg_name])

        while queue:
            item = queue.popleft()
            deps = self.get_installed_package_dependencies(item)

            for pkg in deps:
                if _is_reserved_pkg_name(pkg):
                    continue

                ipkg = self.find_installed_package(pkg)
                # it is possible that this dependency has been removed via zkg

                if not ipkg:
                    errors.append((pkg, "Package not installed."))
                    return errors

                if ipkg.status.is_loaded:
                    queue.append(pkg)

            ipkg = self.find_installed_package(item)

            # it is possible that this package has been removed via zkg
            if not ipkg:
                errors.append((item, "Package not installed."))
                return errors

            if ipkg.status.is_loaded:
                dep_packages = self.list_depender_pkgs(item)

                # check if there is a cyclic dependency
                if item in dep_packages:
                    for dep in dep_packages:
                        if item != dep:
                            ipkg = self.find_installed_package(dep)

                            if ipkg and ipkg.status.is_loaded:
                                self.unload(dep)
                                errors.append((dep, ""))

                    self.unload(item)
                    errors.append((item, ""))
                    continue

                # check if all dependers are unloaded
                if _has_all_dependers_unloaded(item, dep_packages):
                    self.unload(item)
                    errors.append((item, ""))
                    continue

                # package is in use
                dep_packages = self.list_depender_pkgs(pkg_name)
                dep_listing = ""

                for _name in dep_packages:
                    dep_listing += f'"{_name}", '

                errors.append(
                    (
                        item,
                        f"Package is in use by other packages --- {dep_listing[:-2]}.",
                    ),
                )

                return errors

        return errors

    def unload(self, pkg_path):
        """Unmark an installed package as being "loaded".

        The collection of "loaded" packages is a convenient way for Zeek to more
        simply load a whole group of packages installed via the package manager.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            bool: True if a package is successfully unmarked as loaded.

        Raises:
            IOError: if the loader script or manifest can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('unloading "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('unloading "%s": no matching package', pkg_path)
            return False

        if not ipkg.status.is_loaded:
            LOG.debug('unloading "%s": already unloaded', pkg_path)
            return True

        ipkg.status.is_loaded = False
        self._write_autoloader()
        self._write_manifest()
        self._write_plugin_magic(ipkg)
        LOG.debug('unloaded "%s"', pkg_path)
        return True

    def bundle_info(self, bundle_file):
        """Retrieves information on all packages contained in a bundle.

        Args:
            bundle_file (str): the path to the bundle to inspect.

        Returns:
            (str, list of (str, str, :class:`.package.PackageInfo`)): a tuple
            with the the first element set to an empty string if the information
            successfully retrieved, else an error message explaining why the
            bundle file was invalid.  The second element of the tuple is a list
            containing information on each package contained in the bundle:
            the exact git URL and version string from the bundle's manifest
            along with the package info object retrieved by inspecting git repo
            contained in the bundle.
        """
        LOG.debug('getting bundle info for file "%s"', bundle_file)
        bundle_dir = os.path.join(self.scratch_dir, "bundle")
        delete_path(bundle_dir)
        make_dir(bundle_dir)
        infos = []

        try:
            zkg_tarfile_extractall(bundle_file, bundle_dir, umask=self.zkg_umask)
        except Exception as error:
            return (str(error), infos)

        manifest_file = os.path.join(bundle_dir, "manifest.txt")
        config = configparser.ConfigParser(delimiters="=")
        config.optionxform = str

        if not config.read(manifest_file):
            return ("invalid bundle: no manifest file", infos)

        if not config.has_section("bundle"):
            return ("invalid bundle: no [bundle] section in manifest file", infos)

        manifest = config.items("bundle")

        for git_url, version in manifest:
            package = Package(
                git_url=git_url,
                name=git_url.split("/")[-1],
                canonical=True,
            )
            pkg_path = os.path.join(bundle_dir, package.name)
            LOG.debug('getting info for bundled package "%s"', package.name)
            pkg_info = self.info(pkg_path, version=version, prefer_installed=False)
            infos.append((git_url, version, pkg_info))

        return ("", infos)

    def info(self, pkg_path, version="", prefer_installed=True):
        """Retrieves information about a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

            version (str): may be a git version tag, branch name, or commit hash
                from which metadata will be pulled.  If an empty string is
                given, then the latest git version tag is used (or the default
                branch like "main" or "master" if no version tags exist).

            prefer_installed (bool): if this is set, then the information from
                any current installation of the package is returned instead of
                retrieving the latest information from the package's git repo.
                The `version` parameter is also ignored when this is set as
                it uses whatever version of the package is currently installed.

        Returns:
            A :class:`.package.PackageInfo` object.
        """
        pkg_path = canonical_url(pkg_path)
        name = name_from_path(pkg_path)

        if not is_valid_package_name(name):
            reason = f"Package name {name!r} is not valid."
            return PackageInfo(Package(git_url=pkg_path), invalid_reason=reason)

        LOG.debug('getting info on "%s"', pkg_path)

        # Handle built-in packages like installed packages
        # but avoid looking up the repository information.
        bpkg_info = self.find_builtin_package(pkg_path)
        if prefer_installed and bpkg_info:
            return bpkg_info

        ipkg = self.find_installed_package(pkg_path)

        if prefer_installed and ipkg:
            status = ipkg.status
            pkg_name = ipkg.package.name
            clonepath = os.path.join(self.package_clonedir, pkg_name)
            clone = git.Repo(clonepath)
            return _info_from_clone(clone, ipkg.package, status, status.current_version)

        status = None
        matches = self.match_source_packages(pkg_path)

        if not matches:
            package = Package(git_url=pkg_path)

            try:
                return self._info(package, status, version)
            except git.GitCommandError as error:
                LOG.info(
                    'getting info on "%s": invalid git repo path: %s',
                    pkg_path,
                    error,
                )

            LOG.info('getting info on "%s": matched no source package', pkg_path)
            reason = (
                "package name not found in sources and also"
                " not a usable git URL (invalid or inaccessible,"
                " use -vvv for details)"
            )
            return PackageInfo(package=package, invalid_reason=reason, status=status)

        if len(matches) > 1:
            matches_string = [match.qualified_name() for match in matches]
            LOG.info(
                'getting info on "%s": matched multiple packages: %s',
                pkg_path,
                matches_string,
            )
            reason = (
                f'"{pkg_path}" matches multiple packages, '
                f"try a more specific name from: {matches_string}"
            )

            return PackageInfo(invalid_reason=reason, status=status)

        package = matches[0]

        try:
            return self._info(package, status, version)
        except git.GitCommandError as error:
            LOG.info('getting info on "%s": invalid git repo path: %s', pkg_path, error)
            reason = "git repository is either invalid or unreachable"
            return PackageInfo(package=package, invalid_reason=reason, status=status)

    def _info(self, package, status, version):
        """Retrieves information about a package.

        Returns:
            A :class:`.package.PackageInfo` object.

        Raises:
            git.GitCommandError: when failing to clone the package repo
        """
        clonepath = os.path.join(self.scratch_dir, package.name)
        clone = _clone_package(package, clonepath, version)
        versions = git_version_tags(clone)

        if not version:
            if len(versions):
                version = versions[-1]
            else:
                version = git_default_branch(clone)

        try:
            git_checkout(clone, version)
        except git.GitCommandError:
            reason = f'no such commit, branch, or version tag: "{version}"'
            return PackageInfo(package=package, status=status, invalid_reason=reason)

        LOG.debug('checked out "%s", branch/version "%s"', package, version)
        return _info_from_clone(clone, package, status, version)

    def package_versions(self, installed_package):
        """Returns a list of version number tags available for a package.

        Args:
            installed_package (:class:`.package.InstalledPackage`): the package
                for which version number tags will be retrieved.

        Returns:
            list of str: the version number tags.
        """
        name = installed_package.package.name
        clonepath = os.path.join(self.package_clonedir, name)
        clone = git.Repo(clonepath)
        return git_version_tags(clone)

    def validate_dependencies(
        self,
        requested_packages,
        ignore_installed_packages=False,
        ignore_suggestions=False,
        use_builtin_packages=True,
    ):
        """Validates package dependencies.

        Args:
            requested_packages (list of (str, str)): a list of (package name or
                git URL, version) string tuples validate.  If the version string
                is empty, the latest available version of the package is used.


            ignore_installed_packages (bool): whether the dependency analysis
                should consider installed packages as satisfying dependency
                requirements.

            ignore_suggestions (bool): whether the dependency analysis should
                consider installing dependencies that are marked in another
                package's 'suggests' metadata field.

            use_builtin_packages (bool): whether package information from
                builtin packages is used for dependency resolution.

        Returns:
            (str, list of (:class:`.package.PackageInfo`, str, bool)):
            the first element of the tuple is an empty string if dependency
            graph was successfully validated, else an error string explaining
            what is invalid.  In the case it was validated, the second element
            is a list of tuples, each representing a package,  where:

            - The first element is a dependency package that would need to be
              installed in order to satisfy the dependencies of the requested
              packages.

            - The second element of tuples in the list is a version string of
              the associated package that satisfies dependency requirements.

            - The third element of the tuples in the list is a boolean value
              indicating whether the package is included in the list because
              it's merely suggested by another package.

            The list will not include any packages that are already installed or
            that are in the `requested_packages` argument. The list is sorted in
            dependency order: whenever a dependency in turn has dependencies,
            those are guaranteed to appear in order in the list. This means that
            reverse iteration of the list guarantees processing of dependencies
            prior to the depender packages.
        """

        class Node:
            def __init__(self, name):
                self.name = name
                self.info = None
                self.requested_version = None  # (tracking method, version)
                self.installed_version = None  # (tracking method, version)
                self.dependers = {}  # name -> version, name needs self at version
                self.dependees = {}  # name -> version, self needs name at version
                self.is_suggestion = False

            def __str__(self):
                return (
                    f"{self.name}\n\t"
                    f"requested: {self.requested_version}\n\t"
                    f"installed: {self.installed_version}\n\t"
                    f"dependers: {self.dependers}\n\t"
                    f"suggestion: {self.is_suggestion}"
                )

        graph = {}  # Node.name -> Node, nodes store edges
        requests = []  # List of Node, just for requested packages

        # 1. Try to make nodes for everything in the dependency graph...

        # Add nodes for packages that are requested for installation
        for name, version in requested_packages:
            info = self.info(name, version=version, prefer_installed=False)

            if info.invalid_reason:
                return (
                    f'invalid package "{name}": {info.invalid_reason}',
                    [],
                )

            node = Node(info.package.qualified_name())
            node.info = info
            method = node.info.version_type
            node.requested_version = PackageVersion(method, version)
            graph[node.name] = node
            requests.append(node)

        # Recursively add nodes for all dependencies of requested packages,
        to_process = copy.copy(graph)

        while to_process:
            (_, node) = to_process.popitem()
            dd = node.info.dependencies(field="depends")
            ds = node.info.dependencies(field="suggests")

            if dd is None:
                return (
                    f'package "{node.name}" has malformed "depends" field',
                    [],
                )

            all_deps = dd.copy()

            if not ignore_suggestions:
                if ds is None:
                    return (
                        f'package "{node.name}" has malformed "suggests" field',
                        [],
                    )

                all_deps.update(ds)

            for dep_name, _ in all_deps.items():
                if dep_name == "zeek":
                    # A zeek node will get added later.
                    continue

                if dep_name == "zkg":
                    # A zkg node will get added later.
                    continue

                # Suggestion status propagates to 'depends' field of suggested packages.
                is_suggestion = node.is_suggestion or (
                    dep_name in ds and dep_name not in dd
                )

                # If a dependency can be fulfilled by a built-in package
                # use its PackageInfo directly instead of going through
                # self.info() to search for it in package sources, where
                # it may not actually exist.
                info = None
                if use_builtin_packages:
                    info = self.find_builtin_package(dep_name)

                if info is None:
                    info = self.info(dep_name, prefer_installed=False)

                if info.invalid_reason:
                    return (
                        f'package "{node.name}" has invalid dependency "{dep_name}": {info.invalid_reason}',
                        [],
                    )

                dep_name_orig = dep_name
                dep_name = info.package.qualified_name()
                LOG.debug(
                    'dependency "%s" of "%s" resolved to "%s"',
                    dep_name_orig,
                    node.name,
                    dep_name,
                )

                if dep_name in graph:
                    if graph[dep_name].is_suggestion and not is_suggestion:
                        # Suggestion found to be required by another package.
                        graph[dep_name].is_suggestion = False
                    continue

                if dep_name in to_process:
                    if to_process[dep_name].is_suggestion and not is_suggestion:
                        # Suggestion found to be required by another package.
                        to_process[dep_name].is_suggestion = False
                    continue

                node = Node(dep_name)
                node.info = info
                node.is_suggestion = is_suggestion
                graph[node.name] = node
                to_process[node.name] = node

        # Add nodes for things that are already installed (including zeek)
        if not ignore_installed_packages:
            zeek_version = get_zeek_version()

            if zeek_version:
                node = Node("zeek")
                node.installed_version = PackageVersion(
                    TRACKING_METHOD_VERSION,
                    zeek_version,
                )
                graph["zeek"] = node
            else:
                LOG.warning('could not get zeek version: no "zeek-config" in PATH ?')

            node = Node("zkg")
            node.installed_version = PackageVersion(
                TRACKING_METHOD_VERSION,
                __version__,
            )
            graph["zkg"] = node

            for ipkg in self.installed_packages():
                name = ipkg.package.qualified_name()
                status = ipkg.status

                if name not in graph:
                    info = self.info(name, prefer_installed=True)
                    node = Node(name)
                    node.info = info
                    graph[node.name] = node

                graph[name].installed_version = PackageVersion(
                    status.tracking_method,
                    status.current_version,
                )

        # 2. Fill in the edges of the graph with dependency information.
        for name, node in graph.items():
            if name == "zeek":
                continue

            if name == "zkg":
                continue

            dd = node.info.dependencies(field="depends")
            ds = node.info.dependencies(field="suggests")

            if dd is None:
                return (
                    f'package "{node.name}" has malformed "depends" field',
                    [],
                )

            all_deps = dd.copy()

            if not ignore_suggestions:
                if ds is None:
                    return (
                        f'package "{node.name}" has malformed "suggests" field',
                        [],
                    )

                all_deps.update(ds)

            for dep_name, dep_version in all_deps.items():
                if dep_name == "zeek":
                    if "zeek" in graph:
                        graph["zeek"].dependers[name] = dep_version
                        node.dependees["zeek"] = dep_version
                elif dep_name == "zkg":
                    if "zkg" in graph:
                        graph["zkg"].dependers[name] = dep_version
                        node.dependees["zkg"] = dep_version
                else:
                    for _, dependency_node in graph.items():
                        if dependency_node.name == "zeek":
                            continue

                        if dependency_node.name == "zkg":
                            continue

                        if dependency_node.info.package.matches_path(dep_name):
                            dependency_node.dependers[name] = dep_version
                            node.dependees[dependency_node.name] = dep_version
                            break

        # 3. Try to solve for a connected graph with no edge conflicts.

        # Traverse graph in breadth-first order, starting from artificial root
        # with all nodes requested by caller as child nodes.
        nodes_todo = requests

        # The resulting list of packages required to satisfy dependencies,
        # in depender -> dependent (i.e., root -> leaves in dependency tree)
        # order.
        new_pkgs = []

        while nodes_todo:
            node = nodes_todo.pop(0)
            for name in node.dependees:
                nodes_todo.append(graph[name])

            # Avoid cyclic dependencies: ensure we traverse these edges only
            # once. (The graph may well be a dag, so it's okay to encounter
            # specific nodes repeatedly.)
            node.dependees = []

            if not node.dependers:
                if node.installed_version:
                    # We can ignore packages alreaday installed if nothing else
                    # depends on them.
                    continue

                if node.requested_version:
                    # Only the packges requested by the caller have a requested
                    # version. We skip those too if nothing depends on them.
                    continue

                # A new package nothing depends on -- odd?
                new_pkgs.append(
                    (node.info, node.info.best_version(), node.is_suggestion),
                )
                continue

            if node.requested_version:
                # Check that requested version doesn't conflict with dependers.
                for depender_name, version_spec in node.dependers.items():
                    msg, fullfills = node.requested_version.fullfills(version_spec)
                    if not fullfills:
                        return (
                            f'unsatisfiable dependency: requested "{node.name}" ({node.requested_version.version}),'
                            f' but "{depender_name}" requires {version_spec} ({msg})',
                            new_pkgs,
                        )

            elif node.installed_version:
                # Check that installed version doesn't conflict with dependers.
                # track_method, required_version = node.installed_version

                for depender_name, version_spec in node.dependers.items():
                    msg, fullfills = node.installed_version.fullfills(version_spec)
                    if not fullfills:
                        return (
                            f'unsatisfiable dependency: "{node.name}" ({node.installed_version.version}) is installed,'
                            f' but "{depender_name}" requires {version_spec} ({msg})',
                            new_pkgs,
                        )
            else:
                # Choose best version that satisfies constraints
                best_version = None
                need_branch = False
                need_version = False

                def no_best_version_string(node):
                    rval = f'"{node.name}" has no version satisfying dependencies:\n'

                    for depender_name, version_spec in node.dependers.items():
                        rval += f'\t"{depender_name}" requires: "{version_spec}"\n'

                    return rval

                for _, version_spec in node.dependers.items():
                    if version_spec.startswith("branch="):
                        need_branch = True
                    elif version_spec != "*":
                        need_version = True

                if need_branch and need_version:
                    return (no_best_version_string(node), new_pkgs)

                if need_branch:
                    branch_name = None

                    for _, version_spec in node.dependers.items():
                        if version_spec == "*":
                            continue

                        if not branch_name:
                            branch_name = version_spec[len("branch=") :]
                            continue

                        if branch_name != version_spec[len("branch=") :]:
                            return (no_best_version_string(node), new_pkgs)

                    if branch_name:
                        best_version = branch_name
                    else:
                        best_version = node.info.default_branch
                elif need_version:
                    for version in node.info.versions[::-1]:
                        normal_version = normalize_version_tag(version)
                        req_semver = semver.Version.coerce(normal_version)

                        satisfied = True

                        for depender_name, version_spec in node.dependers.items():
                            try:
                                semver_spec = semver.Spec(version_spec)
                            except ValueError:
                                return (
                                    f'package "{depender_name}" has invalid semver spec: {version_spec}',
                                    new_pkgs,
                                )

                            if req_semver not in semver_spec:
                                satisfied = False
                                break

                        if satisfied:
                            best_version = version
                            break

                    if not best_version:
                        return (no_best_version_string(node), new_pkgs)
                else:
                    # Must have been all '*' wildcards or no dependers
                    best_version = node.info.best_version()

                new_pkgs.append((node.info, best_version, node.is_suggestion))

        # Remove duplicate new nodes, preserving their latest (i.e. deepest-in-
        # tree) occurrences. Traversing the resulting list right-to-left guarantees
        # that we never visit a node before we've visited all of its dependees.
        seen_nodes = set()
        res = []

        for it in reversed(new_pkgs):
            if it[0].package.name in seen_nodes:
                continue
            seen_nodes.add(it[0].package.name)
            res.insert(0, it)

        return ("", res)

    def bundle(self, bundle_file, package_list, prefer_existing_clones=False):
        """Creates a package bundle.

        Args:
            bundle_file (str): filesystem path of the zip file to create.

            package_list (list of (str, str)): a list of (git URL, version)
                string tuples to put in the bundle.  If the version string is
                empty, the latest available version of the package is used.

            prefer_existing_clones (bool): if True and the package list contains
                a package at a version that is already installed, then the
                existing git clone of that package is put into the bundle
                instead of cloning from the remote repository.

        Returns:
            str: empty string if the bundle is successfully created,
            else an error string explaining what failed.
        """
        bundle_dir = os.path.join(self.scratch_dir, "bundle")
        delete_path(bundle_dir)
        make_dir(bundle_dir)
        manifest_file = os.path.join(bundle_dir, "manifest.txt")
        config = configparser.ConfigParser(delimiters="=")
        config.optionxform = str
        config.add_section("bundle")

        # To be placed into the meta section.
        builtin_packages = []

        def match_package_url_and_version(git_url, version):
            for ipkg in self.installed_packages():
                if ipkg.package.git_url != git_url:
                    continue

                if ipkg.status.current_version != version:
                    continue

                return ipkg

            return None

        for git_url, version in package_list:
            # Record built-in packages in the bundle's manifest, but
            # otherwise ignore them silently.
            if git_url.startswith(BUILTIN_SCHEME):
                builtin_packages.append((git_url, version))
                continue

            name = name_from_path(git_url)
            clonepath = os.path.join(bundle_dir, name)
            config.set("bundle", git_url, version)

            if prefer_existing_clones:
                ipkg = match_package_url_and_version(git_url, version)

                if ipkg:
                    src = os.path.join(self.package_clonedir, ipkg.package.name)
                    shutil.copytree(src, clonepath, symlinks=True)
                    clone = git.Repo(clonepath)
                    clone.git.reset(hard=True)
                    clone.git.clean("-f", "-x", "-d")

                    for modified_config in self.modified_config_files(ipkg):
                        dst = os.path.join(clonepath, modified_config[0])
                        shutil.copy2(modified_config[1], dst)

                    continue

            try:
                git_clone(git_url, clonepath, shallow=(not is_sha1(version)))
            except git.GitCommandError as error:
                return f"failed to clone {git_url}: {error}"

        # Record the built-in packages expected by this bundle (or simply
        # installed on the source system) in a new [meta] section to aid
        # debugging. This isn't interpreted, but if unbundle produces
        # warnings it may proof helpful.
        if builtin_packages:
            config.add_section("meta")
            entries = []
            for git_url, version in builtin_packages:
                entries.append(f"{name_from_path(git_url)}={version}")

            config.set("meta", "builtin_packages", ",".join(entries))

        with open(manifest_file, "w") as f:
            config.write(f)

        archive = zkg_tarfile_create(bundle_dir)
        delete_path(bundle_file)
        shutil.move(archive, bundle_file)
        return ""

    def unbundle(self, bundle_file):
        """Installs all packages contained within a bundle.

        Args:
            bundle_file (str): the path to the bundle to install.

        Returns:
            str: an empty string if the operation was successful, else an error
            message indicated what went wrong.
        """
        LOG.debug('unbundle "%s"', bundle_file)
        bundle_dir = os.path.join(self.scratch_dir, "bundle")
        delete_path(bundle_dir)
        make_dir(bundle_dir)

        try:
            zkg_tarfile_extractall(bundle_file, bundle_dir, umask=self.zkg_umask)
        except Exception as error:
            return str(error)

        manifest_file = os.path.join(bundle_dir, "manifest.txt")
        config = configparser.ConfigParser(delimiters="=")
        config.optionxform = str

        if not config.read(manifest_file):
            return "invalid bundle: no manifest file"

        if not config.has_section("bundle"):
            return "invalid bundle: no [bundle] section in manifest file"

        manifest = config.items("bundle")

        for git_url, version in manifest:
            package = Package(
                git_url=git_url,
                name=git_url.split("/")[-1],
                canonical=True,
            )

            # Prepare the clonepath with the contents from the bundle.
            clonepath = os.path.join(self.package_clonedir, package.name)
            delete_path(clonepath)
            shutil.move(os.path.join(bundle_dir, package.name), clonepath)

            LOG.debug('unbundle installing "%s"', package.name)
            error = self._install(package, version, use_existing_clone=True)

            if error:
                return error

        # For all the packages that we've just unbundled, verify that their
        # dependencies are fulfilled through installed packages or built-in
        # packages and log a warning if not.
        #
        # Possible reasons are built-in packages on the source system missing
        # on the destination system or usage of --nodeps when creating the bundle.
        for git_url, _ in manifest:
            deps = self.get_installed_package_dependencies(git_url)
            if deps is None:
                LOG.warning('package "%s" not installed?', git_url)
                continue

            for dep, version_spec in deps.items():
                ipkg = self.find_installed_package(dep)
                if ipkg is None:
                    LOG.warning('dependency "%s" of bundled "%s" missing', dep, git_url)
                    continue

                msg, fullfills = ipkg.fullfills(version_spec)
                if not fullfills:
                    LOG.warning(
                        'dependency "%s" (%s) of "%s" not compatible with "%s"',
                        dep,
                        ipkg.status.current_version,
                        git_url,
                        version_spec,
                    )

        return ""

    def test(self, pkg_path, version="", test_dependencies=False):
        """Test a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

            version (str): if not given, then the latest git version tag is
                used (or if no version tags exist, the default branch like
                "main" or "master" is used).  If given, it may be either a git
                version tag or a git branch name.

            test_dependencies (bool): if True, any dependencies required for
                the given package will also get tested. Off by default, meaning
                such dependencies will get locally built and staged, but not
                tested.

        Returns:
            (str, bool, str): a tuple containing an error message string,
            a boolean indicating whether the tests passed, as well as a path
            to the directory in which the tests were run.  In the case
            where tests failed, the directory can be inspected to figure out
            what went wrong.  In the case where the error message string is
            not empty, the error message indicates the reason why tests could
            not be run.  Absence of a test_command in the requested package
            is considered an error.
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('testing "%s"', pkg_path)
        pkg_info = self.info(pkg_path, version=version, prefer_installed=False)

        if pkg_info.invalid_reason:
            return (pkg_info.invalid_reason, "False", "")

        if "test_command" not in pkg_info.metadata:
            return ("Package does not specify a test_command", False, "")

        if not version:
            version = pkg_info.metadata_version

        package = pkg_info.package
        stage = Stage(self, os.path.join(self.package_testdir, package.name))
        stage.populate()

        request = [(package.qualified_name(), version)]
        invalid_deps, new_pkgs = self.validate_dependencies(request, False)

        if invalid_deps:
            return (invalid_deps, False, stage.state_dir)

        env, err = stage.get_subprocess_env()
        if env is None:
            LOG.warning("%s when running tests for %s", err, package.name)
            return (err, False, stage.state_dir)

        pkgs = []
        pkgs.append((pkg_info, version))

        for info, version, _ in new_pkgs:
            pkgs.append((info, version))

        # Clone all packages, checkout right version, and build/install to
        # staging area.
        for info, version in reversed(pkgs):
            LOG.debug(
                'preparing "%s" for testing: version %s',
                info.package.name,
                version,
            )
            clonepath = os.path.join(stage.clone_dir, info.package.name)

            # After we prepared the stage, the clonepath might exist (as a
            # symlink to the installed-version package clone) if we're testing
            # an alternative version of an installed package.  Remove the
            # symlink.
            if os.path.islink(clonepath):
                delete_path(clonepath)

            try:
                clone = _clone_package(info.package, clonepath, version)
            except git.GitCommandError as error:
                LOG.warning("failed to clone git repo: %s", error)
                return (
                    f"failed to clone {info.package.git_url}",
                    False,
                    stage.state_dir,
                )

            try:
                git_checkout(clone, version)
            except git.GitCommandError as error:
                LOG.warning("failed to checkout git repo version: %s", error)
                return (
                    f"failed to checkout {version} of {info.package.git_url}",
                    False,
                    stage.state_dir,
                )

            with UmaskContext(self.zkg_umask):
                fail_msg = self._stage(info.package, version, clone, stage, env)

            if fail_msg:
                return (fail_msg, False, self.state_dir)

        # Finally, run tests (with correct environment set)
        if test_dependencies:
            test_pkgs = pkgs
        else:
            test_pkgs = [(pkg_info, version)]

        for info, _ in reversed(test_pkgs):
            LOG.info('testing "%s"', package)
            # Interpolate the test command:
            metadata, invalid_reason = self._interpolate_package_metadata(
                info.metadata,
                stage,
            )
            if invalid_reason:
                return (invalid_reason, False, stage.state_dir)

            if "test_command" not in metadata:
                LOG.info(
                    'Skipping unit tests for "%s": no test_command in metadata',
                    info.package.qualified_name(),
                )
                continue

            test_command = metadata["test_command"]

            cwd = os.path.join(stage.clone_dir, info.package.name)
            outfile = os.path.join(cwd, "zkg.test_command.stdout")
            errfile = os.path.join(cwd, "zkg.test_command.stderr")

            LOG.debug(
                'running test_command for %s with cwd="%s", PATH="%s",'
                ' and ZEEKPATH="%s": %s',
                info.package.name,
                cwd,
                env["PATH"],
                env["ZEEKPATH"],
                test_command,
            )

            with open(outfile, "w") as test_stdout, open(errfile, "w") as test_stderr:
                cmd = subprocess.Popen(
                    test_command,
                    shell=True,
                    cwd=cwd,
                    env=env,
                    stdout=test_stdout,
                    stderr=test_stderr,
                )

            rc = cmd.wait()

            if rc != 0:
                return (
                    f"test_command failed with exit code {rc}",
                    False,
                    stage.state_dir,
                )

        return ("", True, stage.state_dir)

    def _get_executables(self, metadata):
        return metadata.get("executables", "").split()

    def _stage(self, package, version, clone, stage, env=None):
        """Stage a package.

        Staging is the act of getting a package ready for use at a particular
        location in the file system, called a "stage". The stage may be the
        actual installation folders for the system's Zeek distribution, or one
        purely internal to zkg's stage management when testing a package. The
        steps involved in staging include cloning and checking out the package
        at the desired version, building it if it features a build_command, and
        installing script & plugin folders inside the requested stage.

        Args:
            package (:class:`.package.Package`): the package to stage

            version (str): the git tag, branch name, or commit hash of the
                package version to stage

            clone (:class:`git.Repo`): the on-disk clone of the package's
                git repository.

            stage (:class:`Stage`): the staging object describing the disk
                locations for installation.

            env (dict of str -> str): an optional environment to pass to the
                child process executing the package's build_command, if any.
                If None, the current environment is used.

        Returns:
            str: empty string if staging succeeded, otherwise an error string
            explaining why it failed.

        """
        LOG.debug('staging "%s": version %s', package, version)
        metadata_file = _pick_metadata_file(clone.working_dir)
        metadata_parser = configparser.ConfigParser(interpolation=None)
        invalid_reason = _parse_package_metadata(metadata_parser, metadata_file)
        if invalid_reason:
            return invalid_reason

        metadata = _get_package_metadata(metadata_parser)
        metadata, invalid_reason = self._interpolate_package_metadata(metadata, stage)
        if invalid_reason:
            return invalid_reason

        build_command = metadata.get("build_command", "")
        if build_command:
            LOG.debug(
                'building "%s": running build_command: %s',
                package,
                build_command,
            )
            bufsize = 4096

            with UmaskContext(self.zkg_umask):
                build = subprocess.Popen(
                    build_command,
                    shell=True,
                    cwd=clone.working_dir,
                    env=env,
                    bufsize=bufsize,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            try:
                buildlog = self.package_build_log(clone.working_dir)

                with open(buildlog, "wb") as f:
                    LOG.info(
                        'installing "%s": writing build log: %s',
                        package,
                        buildlog,
                    )

                    f.write("=== STDERR ===\n".encode(std_encoding(sys.stderr)))

                    while True:
                        data = build.stderr.read(bufsize)

                        if data:
                            f.write(data)
                        else:
                            break

                    f.write("=== STDOUT ===\n".encode(std_encoding(sys.stdout)))

                    while True:
                        data = build.stdout.read(bufsize)

                        if data:
                            f.write(data)
                        else:
                            break

            except OSError as error:
                LOG.warning(
                    'installing "%s": failed to write build log %s %s: %s',
                    package,
                    buildlog,
                    error.errno,
                    error.strerror,
                )

            returncode = build.wait()

            if returncode != 0:
                return f"package build_command failed, see log in {buildlog}"

        pkg_script_dir = metadata.get("script_dir", "")
        script_dir_src = os.path.join(clone.working_dir, pkg_script_dir)
        script_dir_dst = os.path.join(stage.script_dir, package.name)

        if not os.path.exists(script_dir_src):
            return f"package's 'script_dir' does not exist: {pkg_script_dir}"

        pkgload = os.path.join(script_dir_src, "__load__.zeek")

        if os.path.isfile(pkgload):
            try:
                symlink_path = os.path.join(
                    os.path.dirname(stage.script_dir),
                    package.name,
                )
                make_symlink(os.path.join("packages", package.name), symlink_path)

                for alias in aliases(metadata):
                    symlink_path = os.path.join(
                        os.path.dirname(stage.script_dir),
                        alias,
                    )
                    make_symlink(os.path.join("packages", package.name), symlink_path)

            except OSError as exception:
                error = f"could not create symlink at {symlink_path}"
                error += f": {type(exception).__name__}: {exception}"
                return error

            error = _copy_package_dir(
                package,
                "script_dir",
                script_dir_src,
                script_dir_dst,
                self.scratch_dir,
            )

            if error:
                return error
        else:
            if "script_dir" in metadata:
                return f"no __load__.zeek file found in package's 'script_dir' : {pkg_script_dir}"

            LOG.warning(
                'installing "%s": no __load__.zeek in implicit'
                " script_dir, skipped installing scripts",
                package,
            )

        pkg_plugin_dir = metadata.get("plugin_dir", "build")
        plugin_dir_src = os.path.join(clone.working_dir, pkg_plugin_dir)
        plugin_dir_dst = os.path.join(stage.plugin_dir, package.name)

        if not os.path.exists(plugin_dir_src):
            LOG.info(
                'installing "%s": package "plugin_dir" does not exist: %s',
                package,
                pkg_plugin_dir,
            )

            if pkg_plugin_dir != "build":
                # It's common for a package to not have build directory for
                # plugins, so don't error out in that case, just log it.
                return f"package's 'plugin_dir' does not exist: {pkg_plugin_dir}"

        error = _copy_package_dir(
            package,
            "plugin_dir",
            plugin_dir_src,
            plugin_dir_dst,
            self.scratch_dir,
        )

        if error:
            return error

        # Ensure any listed executables exist as advertised.
        for p in self._get_executables(metadata):
            full_path = os.path.join(clone.working_dir, p)
            if not os.path.isfile(full_path):
                return f"executable '{p}' is missing"

            if not os.access(full_path, os.X_OK):
                return f"file '{p}' is not executable"

            if stage.bin_dir is not None:
                make_symlink(
                    full_path,
                    os.path.join(stage.bin_dir, os.path.basename(p)),
                    force=True,
                )

        return ""

    def install(self, pkg_path, version=""):
        """Install a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

            version (str): if not given, then the latest git version tag is
                installed (or if no version tags exist, the default branch like
                "main" or "master" is installed).  If given, it may be either a
                git version tag, a git branch name, or a git commit hash.

        Returns:
            str: empty string if package installation succeeded else an error
            string explaining why it failed.

        Raises:
            IOError: if the manifest can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('installing "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if ipkg:
            conflict = ipkg.package

            if conflict.qualified_name().endswith(pkg_path):
                LOG.debug('installing "%s": re-install: %s', pkg_path, conflict)
                clonepath = os.path.join(self.package_clonedir, conflict.name)
                _clone_package(conflict, clonepath, version)
                return self._install(conflict, version)

            LOG.info(
                'installing "%s": matched already installed package: %s',
                pkg_path,
                conflict,
            )
            return (
                f'package with name "{conflict.name}" ({conflict}) is already installed'
            )

        matches = self.match_source_packages(pkg_path)

        if not matches:
            try:
                package = Package(git_url=pkg_path)
                return self._install(package, version)
            except git.GitCommandError as error:
                LOG.info('installing "%s": invalid git repo path: %s', pkg_path, error)

            LOG.info('installing "%s": matched no source package', pkg_path)
            return "package not found in sources and also not a valid git URL"

        if len(matches) > 1:
            matches_string = [match.qualified_name() for match in matches]
            LOG.info(
                'installing "%s": matched multiple packages: %s',
                pkg_path,
                matches_string,
            )

            return (
                f'"{pkg_path}" matches multiple packages, '
                f"try a more specific name from: {matches_string}"
            )

        try:
            return self._install(matches[0], version)
        except git.GitCommandError as error:
            LOG.warning('installing "%s": source package git repo is invalid', pkg_path)
            return f'failed to clone package "{pkg_path}": {error}'

    def _validate_alias_conflict(self, pkg, metadata_dict):
        """Check if there's an alias conflict.

        If any of the installed packages aliases collide with the package's
        name or its aliases, return a string describing the issue.

        Args:
            package (:class:`.package.Package`): the package to be installed

            metadata_dict (dict): The metadata for the given package.
                package.metadata may not be valid yet.

        Returns:
            str: empty string on success, else descriptive error message.
        """
        package_names = {}
        alias_names = {}
        for ipkg in self.installed_packages():
            if ipkg.package == pkg:
                continue

            qn = ipkg.package.qualified_name()

            package_names[ipkg.package.name] = qn
            for ipkg_alias in ipkg.package.aliases():
                alias_names[ipkg_alias] = qn

        # Is the new package's name the same as an existing alias?
        if pkg.name in alias_names:
            qn = alias_names[pkg.name]
            return f'name "{pkg.name}" conflicts with alias from "{qn}"'

        # Any of the aliases matching another package's name or another alias?
        for alias in aliases(metadata_dict):
            if alias in package_names:
                qn = package_names[alias]
                return (
                    f'alias "{alias}" conflicts with name of installed package "{qn}"'
                )

            if alias in alias_names:
                qn = alias_names[alias]
                return (
                    f'alias "{alias}" conflicts with alias of installed package "{qn}"'
                )

        return ""

    def _install(self, package, version, use_existing_clone=False):
        """Install a :class:`.package.Package`.

        Returns:
            str: empty string if package installation succeeded else an error
            string explaining why it failed.

        Raises:
            git.GitCommandError: if the git repo is invalid
            IOError: if the package manifest file can't be written
        """
        clonepath = os.path.join(self.package_clonedir, package.name)
        ipkg = self.find_installed_package(package.name)

        if use_existing_clone or ipkg:
            clone = git.Repo(clonepath)
        else:
            clone = _clone_package(package, clonepath, version)

        status = PackageStatus()
        status.is_loaded = ipkg.status.is_loaded if ipkg else False
        status.is_pinned = ipkg.status.is_pinned if ipkg else False

        version_tags = git_version_tags(clone)

        if version:
            if _is_commit_hash(clone, version):
                status.tracking_method = TRACKING_METHOD_COMMIT
            elif version in version_tags:
                status.tracking_method = TRACKING_METHOD_VERSION
            else:
                branches = _get_branch_names(clone)

                if version in branches:
                    status.tracking_method = TRACKING_METHOD_BRANCH
                else:
                    LOG.info(
                        'branch "%s" not in available branches: %s',
                        version,
                        branches,
                    )
                    return f'no such branch or version tag: "{version}"'

        else:
            if len(version_tags):
                version = version_tags[-1]
                status.tracking_method = TRACKING_METHOD_VERSION
            else:
                version = git_default_branch(clone)
                status.tracking_method = TRACKING_METHOD_BRANCH

        status.current_version = version
        git_checkout(clone, version)
        status.current_hash = clone.head.object.hexsha
        status.is_outdated = _is_clone_outdated(clone, version, status.tracking_method)

        metadata_file = _pick_metadata_file(clone.working_dir)
        metadata_parser = configparser.ConfigParser(interpolation=None)
        invalid_reason = _parse_package_metadata(metadata_parser, metadata_file)

        if invalid_reason:
            return invalid_reason

        raw_metadata = _get_package_metadata(metadata_parser)

        invalid_reason = self._validate_alias_conflict(package, raw_metadata)

        if invalid_reason:
            return invalid_reason

        # A dummy stage that uses the actual installation folders;
        # we do not need to populate() it.
        stage = Stage(self)
        with UmaskContext(self.zkg_umask):
            fail_msg = self._stage(package, version, clone, stage)
        if fail_msg:
            return fail_msg

        if not package.source:
            # If installing directly from git URL, see if it actually is found
            # in a package source and fill in those details.
            for pkg in self.source_packages():
                if pkg.git_url == package.git_url:
                    package.source = pkg.source
                    package.directory = pkg.directory
                    package.metadata = pkg.metadata
                    break

        package.metadata = raw_metadata
        self.installed_pkgs[package.name] = InstalledPackage(package, status)
        self._write_manifest()
        self._refresh_bin_dir(self.bin_dir)
        LOG.debug('installed "%s"', package)
        return ""

    def _interpolate_package_metadata(self, metadata, stage):
        # This is a bit circular: we need to parse the user variables, if any,
        # from the metadata before we can substitute them into other package
        # metadata.

        requested_user_vars = UserVar.parse_dict(metadata)
        if requested_user_vars is None:
            return None, "package has malformed 'user_vars' metadata field"

        substitutions = {
            "zeek_dist": self.zeek_dist,
            "package_base": stage.clone_dir,
        }

        substitutions.update(self.user_vars)

        for uvar in requested_user_vars:
            val_from_env = os.environ.get(uvar.name())

            if val_from_env:
                substitutions[uvar.name()] = val_from_env

            if uvar.name() not in substitutions:
                substitutions[uvar.name()] = uvar.val()

        # Now apply the substitutions via a new config parser:
        metadata_parser = configparser.ConfigParser(defaults=substitutions)
        metadata_parser.read_dict({"package": metadata})

        return _get_package_metadata(metadata_parser), None

    # Ensure we have links in bin_dir for all executables coming with any of
    # the currently installed packages.
    def _refresh_bin_dir(self, bin_dir, prev_bin_dir=None):
        for ipkg in self.installed_pkgs.values():
            for exe in self._get_executables(ipkg.package.metadata):
                # Put symlinks in place that are missing in current directory
                src = os.path.join(self.package_clonedir, ipkg.package.name, exe)
                dst = os.path.join(bin_dir, os.path.basename(exe))

                if (
                    not os.path.exists(dst)
                    or not os.path.islink(dst)
                    or os.path.realpath(src) != os.path.realpath(dst)
                ):
                    LOG.debug("creating link %s -> %s", src, dst)
                    make_symlink(src, dst, force=True)
                else:
                    LOG.debug("link %s is up to date", dst)

    # Remove all links in bin_dir that are associated with executables
    # coming with any of the currently installed package.
    def _clear_bin_dir(self, bin_dir):
        for ipkg in self.installed_pkgs.values():
            for exe in self._get_executables(ipkg.package.metadata):
                old = os.path.join(bin_dir, os.path.basename(exe))
                if os.path.islink(old):
                    try:
                        os.unlink(old)
                        LOG.debug("removed link %s", old)
                    except Exception:
                        LOG.warn("failed to remove link %s", old)


def _get_branch_names(clone):
    rval = []

    for ref in clone.references:
        branch_name = str(ref.name)

        if not branch_name.startswith("origin/"):
            continue

        rval.append(branch_name.split("origin/")[1])

    return rval


def _is_version_outdated(clone, version):
    version_tags = git_version_tags(clone)
    latest = normalize_version_tag(version_tags[-1])
    return normalize_version_tag(version) != latest


def _is_branch_outdated(clone, branch):
    it = clone.iter_commits(f"{branch}..origin/{branch}")
    num_commits_behind = sum(1 for c in it)
    return num_commits_behind > 0


def _is_clone_outdated(clone, ref_name, tracking_method):
    if tracking_method == TRACKING_METHOD_VERSION:
        return _is_version_outdated(clone, ref_name)

    if tracking_method == TRACKING_METHOD_BRANCH:
        return _is_branch_outdated(clone, ref_name)

    if tracking_method == TRACKING_METHOD_COMMIT:
        return False

    raise NotImplementedError


def _is_commit_hash(clone, text):
    try:
        commit = clone.commit(text)
        return commit.hexsha.startswith(text)
    except Exception:
        return False


def _copy_package_dir(package, dirname, src, dst, scratch_dir):
    """Copy a directory from a package to its installation location.

    Returns:
        str: empty string if package dir copy succeeded else an error string
        explaining why it failed.
    """
    if not os.path.exists(src):
        return ""

    if os.path.isfile(src) and tarfile.is_tarfile(src):
        tmp_dir = os.path.join(scratch_dir, "untar")
        delete_path(tmp_dir)
        make_dir(tmp_dir)

        try:
            zkg_tarfile_extractall(src, tmp_dir)
        except Exception as error:
            return str(error)

        ld = os.listdir(tmp_dir)

        if len(ld) != 1:
            # Apple `tar` might store HFS+ extended metadata in tar files.
            # These metadata files have the names `._FOO` for each entry `FOO`.
            # Since we expect a single top-level directory for the extracted
            # plugin, ignore the metadata file if we see it.
            ld.sort()
            if len(ld) == 2 and ld[0] == f"._{ld[1]}":
                ld = ld[1:]
            else:
                return f"failed to copy package {dirname}: invalid tarfile"

        src = os.path.join(tmp_dir, ld[0])

    if not os.path.isdir(src):
        return f"failed to copy package {dirname}: not a dir or tarfile"

    def ignore(_, files):
        rval = []

        for f in files:
            if f in {".git", "bro-pkg.meta", "zkg.meta"}:
                rval.append(f)

        return rval

    try:
        copy_over_path(src, dst, ignore=ignore)
    except shutil.Error as error:
        errors = error.args[0]
        reasons = ""

        for err in errors:
            src, dst, msg = err
            reason = f"failed to copy {dirname}: {src} -> {dst}: {msg}"
            reasons += "\n" + reason
            LOG.warning('installing "%s": %s', package, reason)

        return f"failed to copy package {dirname}: {reasons}"

    return ""


def _create_readme(file_path):
    if os.path.exists(file_path):
        return

    with open(file_path, "w") as f:
        f.write("WARNING: This directory is managed by zkg.\n")
        f.write("Don't make direct modifications to anything within it.\n")


def _clone_package(package, clonepath, version):
    """Clone a :class:`.package.Package` git repo.

    Returns:
        git.Repo: the cloned package

    Raises:
        git.GitCommandError: if the git repo is invalid
    """
    delete_path(clonepath)
    shallow = not is_sha1(version)
    return git_clone(package.git_url, clonepath, shallow=shallow)


def _get_package_metadata(parser):
    return {item[0]: item[1] for item in parser.items("package")}


def _pick_metadata_file(directory):
    rval = os.path.join(directory, METADATA_FILENAME)

    if os.path.exists(rval):
        return rval

    return os.path.join(directory, LEGACY_METADATA_FILENAME)


def _parse_package_metadata(parser, metadata_file):
    """Return string explaining why metadata is invalid, or '' if valid."""
    if not parser.read(metadata_file):
        LOG.warning("%s: missing metadata file", metadata_file)
        return (
            f"missing {METADATA_FILENAME} (or {LEGACY_METADATA_FILENAME}) metadata file"
        )

    if not parser.has_section("package"):
        LOG.warning("%s: metadata missing [package]", metadata_file)
        return f"{os.path.basename(metadata_file)} is missing [package] section"

    for a in aliases(_get_package_metadata(parser)):
        if not is_valid_package_name(a):
            return f'invalid alias "{a}"'

    return ""


_legacy_metadata_warnings = set()


def _info_from_clone(clone, package, status, version):
    """Retrieves information about a package.

    Returns:
        A :class:`.package.PackageInfo` object.
    """
    versions = git_version_tags(clone)
    default_branch = git_default_branch(clone)

    if _is_commit_hash(clone, version):
        version_type = TRACKING_METHOD_COMMIT
    elif version in versions:
        version_type = TRACKING_METHOD_VERSION
    else:
        version_type = TRACKING_METHOD_BRANCH

    metadata_file = _pick_metadata_file(clone.working_dir)
    metadata_parser = configparser.ConfigParser(interpolation=None)
    invalid_reason = _parse_package_metadata(metadata_parser, metadata_file)

    if invalid_reason:
        return PackageInfo(
            package=package,
            invalid_reason=invalid_reason,
            status=status,
            versions=versions,
            metadata_version=version,
            version_type=version_type,
            metadata_file=metadata_file,
            default_branch=default_branch,
        )

    if (
        os.path.basename(metadata_file) == LEGACY_METADATA_FILENAME
        and package.qualified_name() not in _legacy_metadata_warnings
    ):
        LOG.warning(
            "Package %s is using the legacy bro-pkg.meta metadata file. "
            "While bro-pkg.meta still functions, it is recommended to "
            "use zkg.meta instead for future-proofing. Please report this "
            "to the package maintainers.",
            package.qualified_name(),
        )
        _legacy_metadata_warnings.add(package.qualified_name())

    metadata = _get_package_metadata(metadata_parser)

    return PackageInfo(
        package=package,
        invalid_reason=invalid_reason,
        status=status,
        metadata=metadata,
        versions=versions,
        metadata_version=version,
        version_type=version_type,
        metadata_file=metadata_file,
        default_branch=default_branch,
    )


def _is_reserved_pkg_name(name):
    return name == "zeek" or name == "zkg"
