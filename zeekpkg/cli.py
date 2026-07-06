"""The command-line interface for zkg.

This includes the argument parser and its command implementations.
"""

import argparse
import configparser
import filecmp
import io
import json
import os
import re
import shutil
import subprocess
import sys
from collections import OrderedDict
from typing import Any

from ._util import (
    active_git_branch,
    check_local_git_repo,
    delete_path,
    find_program,
    make_dir,
    read_zeek_config_line,
)
from .config import (
    CONFIG,
)
from .consts import (
    VERSION,
    ZKG_DEFAULT_SOURCE,
    ZKG_DEFAULT_TEMPLATE,
)
from .logs import LOG
from .manager import Manager
from .package import (
    BUILTIN_SCHEME,
    TRACKING_METHOD_VERSION,
    InstalledPackage,
    Package,
    PackageInfo,
    name_from_path,
)
from .template import (
    InputError,
    LoadError,
    OutputError,
    Template,
)
from .ui import (
    UI,
)
from .uservar import (
    UserVar,
)


def prompt_for_user_vars(
    manager: Manager,
    args: argparse.Namespace,
    pkg_infos: list[PackageInfo],
) -> None:
    answers: dict[str, str] = {}

    for info in pkg_infos:
        name = info.package.qualified_name()
        requested_user_vars = info.user_vars()

        if requested_user_vars is None:
            UI.error(f'malformed user_vars in "{name}"')
            sys.exit(1)

        for uvar in requested_user_vars:
            try:
                answers[uvar.name()] = uvar.resolve(
                    name,
                    args.user_var,
                    args.force,
                )
            except ValueError:
                UI.error(
                    f'could not determine value of user variable "{uvar.name()}",'
                    " provide via environment or --user-var",
                )
                sys.exit(1)

    for key, value in answers.items():
        CONFIG.set("user_vars", key, value)

    if not args.force and answers:
        configfile = CONFIG.find_configfile(args)
        if not configfile:
            UI.warning("could not find config file to save to.")
            return
        if CONFIG.save(configfile):
            UI.info(f"Saved answers to config file: {configfile}")


def get_changed_state(
    manager: Manager,
    saved_state: dict[str, bool],
    pkg_lst: list[str],
) -> str:
    """Returns the list of packages that have changed loaded state.

    Args:
        saved_state (dict): dictionary of saved load state for installed
        packages.

        pkg_lst (list): list of package names to be skipped

    Returns:
        dep_listing (str): string installed packages that have changed state

    """
    _lst = [name_from_path(_pkg_path) for _pkg_path in pkg_lst]
    dep_listing = ""

    for _pkg_name in sorted(manager.installed_package_dependencies()):
        if _pkg_name in _lst:
            continue

        _ipkg = manager.find_installed_package(_pkg_name)

        if not _ipkg or _ipkg.status.is_loaded == saved_state[_pkg_name]:
            continue

        dep_listing += f"  {_pkg_name}\n"

    return dep_listing


def cmd_test(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    if args.version and len(args.package) > 1:
        UI.error('test --version" may only be used for a single package')
        sys.exit(1)

    package_infos: list[tuple[PackageInfo, str]] = []

    for name in args.package:
        if not check_local_git_repo(name):
            sys.exit(1)

        # If the package to be tested is included with Zeek, don't allow
        # to run tests due to the potential of conflicts.
        bpkg_info = manager.find_builtin_package(name)
        if bpkg_info is not None:
            UI.warning(f'cannot run tests for "{name}": built-in package')
            sys.exit(1)

        version = args.version if args.version else active_git_branch(name)
        package_info = manager.info(name, version=version, prefer_installed=False)

        if package_info.invalid_reason:
            UI.error(f'invalid package "{name}": {package_info.invalid_reason}')
            sys.exit(1)

        if not version:
            version = package_info.best_version()

        package_infos.append((package_info, version))

    all_passed = True

    for info, version in package_infos:
        name = info.package.qualified_name()

        if "test_command" not in info.metadata:
            UI.info(f"{name}: no test_command found in metadata, skipping")
            continue

        error_msg, passed, test_dir = manager.test(
            name,
            version,
            test_dependencies=True,
        )

        if error_msg:
            all_passed = False
            UI.error(f'failed to run tests for "{name}": {error_msg}')
            continue

        if passed:
            UI.info(f"{name}: all tests passed")
        else:
            all_passed = False
            UI.error(
                f'error: package "{name}" tests failed, inspect'
                f" {manager.package_test_log(info.package.name)} and"
                f" the contents of {test_dir}",
            )

    if not all_passed:
        sys.exit(1)


def cmd_install(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    if args.version and len(args.package) > 1:
        UI.error('"install --version" may only be used for a single package')
        sys.exit(1)

    package_infos: list[tuple[PackageInfo, str, bool]] = []

    # For each package, check validity if local, and determine the
    # effective version to install.
    for name in args.package:
        if not check_local_git_repo(name):
            sys.exit(1)

        # Outright prevent installing a package that Zeek has built-in.
        bpkg_info = manager.find_builtin_package(name)
        if bpkg_info is not None:
            UI.warning(f'cannot install "{name}": built-in package')
            sys.exit(1)

        version = args.version if args.version else active_git_branch(name)
        package_info = manager.info(name, version=version, prefer_installed=False)

        if package_info.invalid_reason:
            UI.error(f'invalid package "{name}": {package_info.invalid_reason}')
            sys.exit(1)

        if not version:
            version = package_info.best_version()

        package_infos.append((package_info, version, False))

    # We modify package_infos below, so copy current state to preserve list of
    # packages requested by caller:
    orig_pkgs = package_infos.copy()
    new_pkgs: list[tuple[PackageInfo, str, bool]] = []

    # Resolve dependencies.
    if not args.nodeps:
        to_validate = []
        for info, version, _ in package_infos:
            to_validate.append((info.package.qualified_name(), version))
        invalid_reason, new_pkgs = manager.validate_dependencies(
            to_validate,
            ignore_suggestions=args.nosuggestions,
        )

        if invalid_reason:
            UI.error("failed to resolve dependencies:", invalid_reason)
            sys.exit(1)

    # Report what we're about to do and obtain confirmation, unless suppressed.
    if not args.force:
        package_listing = ""

        for info, version, _ in sorted(package_infos, key=lambda x: x[0].package.name):
            name = info.package.qualified_name()
            package_listing += f"  {name} ({version})\n"

        UI.info("The following packages will be INSTALLED:")
        UI.info(package_listing)

        if new_pkgs:
            dependency_listing = ""

            for info, version, suggested in sorted(
                new_pkgs,
                key=lambda x: x[0].package.name,
            ):
                name = info.package.qualified_name()
                dependency_listing += f"  {name} ({version})"

                if suggested:
                    dependency_listing += " (suggested)"

                dependency_listing += "\n"

            UI.info("The following dependencies will be INSTALLED:")
            UI.info(dependency_listing)

        allpkgs = package_infos + new_pkgs
        extdep_listing = ""

        for info, version, _ in sorted(allpkgs, key=lambda x: x[0].package.name):
            name = info.package.qualified_name()
            extdeps = info.dependencies(field="external_depends")

            if extdeps is None:
                extdep_listing += f"  from {name} ({version}):\n    <malformed>\n"
                continue

            if extdeps:
                extdep_listing += f"  from {name} ({version}):\n"

                for extdep, semver in sorted(extdeps.items()):
                    extdep_listing += f"    {extdep} {semver}\n"

        if extdep_listing:
            UI.info(
                "Verify the following REQUIRED external dependencies:\n"
                "(Ensure their installation on all relevant systems before"
                " proceeding):",
            )
            UI.info(extdep_listing)

        if not UI.confirmation_prompt("Proceed?"):
            return

    package_infos += new_pkgs

    prompt_for_user_vars(
        manager,
        args,
        [info for info, _, _ in package_infos],
    )

    # Run tests on explicitly requested packages. With --force, this
    # exits with error if tests fail.
    # XXX this prints output even with --force
    if not args.skiptests:
        # Iterate only over the requested packages here, skipping the
        # dependencies. We ask the manager to include dependencies below.
        for info, version, _ in orig_pkgs:
            name = info.package.qualified_name()

            if "test_command" not in info.metadata:
                LOG.info(
                    f'Skipping unit tests for "{name}": no test_command in metadata',
                )
                continue

            UI.info(f'Running unit tests for "{name}"')
            error_msg = ""
            # For testing we always process dependencies, since the tests might
            # well fail without them. If the user wants --nodeps and the tests
            # fail because of it, they'll also need to say --skiptests.
            error, passed, test_dir = manager.test(
                name,
                version,
                test_dependencies=True,
            )
            if error:
                error_msg = f"failed to run tests for {name}: {error}"
            elif not passed:
                error_msg = (
                    f'"{name}" tests failed, inspect'
                    f" {manager.package_test_log(info.package.name)} and"
                    f" the contents of {test_dir}"
                )

            if error_msg:
                UI.error(error_msg)

                if args.force:
                    sys.exit(1)

                if not UI.confirmation_prompt(
                    "Proceed to install anyway?",
                    default_to_yes=False,
                ):
                    return

    installs_failed = []

    # Install packages in reverse order, which likely is supposed to mean
    # dependencies-first?
    for info, version, _ in reversed(package_infos):
        name = info.package.qualified_name()

        is_overwriting = False
        ipkg = manager.find_installed_package(name)

        modifications = []
        backup_files = []
        prev_upstream_config_files = []

        if ipkg:
            is_overwriting = True
            modifications = manager.modified_config_files(ipkg)
            backup_files = manager.backup_modified_files(name, modifications)
            prev_upstream_config_files = manager.save_temporary_config_files(ipkg)

        UI.info(f'Installing "{name}"', flush=True)
        # Use default arguments here to avoid late-binding closure:
        err = UI.call_activity(lambda n=name, v=version: manager.install(n, v))

        if err:
            UI.info(f'Failed installing "{name}": {err}')
            installs_failed.append((name, version))
            continue

        ipkg = manager.find_installed_package(name)
        UI.info(f'Installed "{name}" ({ipkg.status.current_version if ipkg else ""})')

        if is_overwriting:
            for i, mf in enumerate(modifications):
                next_upstream_config_file = mf[1]

                if not os.path.isfile(next_upstream_config_file):
                    UI.info("\tConfig file no longer exists:")
                    UI.info("\t\t" + next_upstream_config_file)
                    UI.info("\tPrevious, locally modified version backed up to:")
                    UI.info("\t\t" + backup_files[i])
                    continue

                prev_upstream_config_file = prev_upstream_config_files[i][1]

                if filecmp.cmp(prev_upstream_config_file, next_upstream_config_file):
                    # Safe to restore user's version
                    shutil.copy2(backup_files[i], next_upstream_config_file)
                    continue

                UI.info("\tConfig file has been overwritten with a different version:")
                UI.info("\t\t" + next_upstream_config_file)
                UI.info("\tPrevious, locally modified version backed up to:")
                UI.info("\t\t" + backup_files[i])

        if ipkg and manager.has_scripts(ipkg):
            load_error = manager.load(name)

            if load_error:
                UI.info(f'Failed loading "{name}": {load_error}')
            else:
                UI.info(f'Loaded "{name}"')

    if not args.nodeps:
        # Now load runtime dependencies after all dependencies and suggested
        # packages have been installed and loaded.
        for info, _, _ in sorted(orig_pkgs, key=lambda x: x[0].package.name):
            _listing, saved_state = "", manager.loaded_package_states()
            name = info.package.qualified_name()

            load_error2 = manager.load_with_dependencies(
                name_from_path(name),
            )

            for _name, _error in load_error2:
                if not _error:
                    _listing += f"  {_name}\n"

            if not _listing:
                dep_listing = get_changed_state(manager, saved_state, [name])

                if dep_listing:
                    UI.info(
                        "The following installed packages were additionally "
                        "loaded to satisfy runtime dependencies",
                    )
                    UI.info(dep_listing)

            else:
                UI.info(
                    "The following installed packages could NOT be loaded "
                    f'to satisfy runtime dependencies for "{name}"',
                )
                UI.info(_listing)
                manager.restore_loaded_package_states(saved_state)

    if installs_failed:
        UI.error(
            "incomplete installation, the follow packages failed to be installed:",
        )

        for n, v in installs_failed:
            UI.error(f"  {n} ({v})", prefix="")

        sys.exit(1)


def cmd_bundle(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    packages_to_bundle = []
    prefer_existing_clones = False

    if args.manifest:
        if len(args.manifest) == 1 and os.path.isfile(args.manifest[0]):
            config = configparser.ConfigParser(delimiters="=")
            config.optionxform = lambda optionstr: optionstr  # type: ignore

            if config.read(args.manifest[0]) and config.has_section("bundle"):
                packages = config.items("bundle")
            else:
                UI.error(f'"{args.manifest[0]}" is not a valid manifest file')
                sys.exit(1)

        else:
            packages = [(name, "") for name in args.manifest]

        to_validate = []
        new_pkgs: list[tuple[PackageInfo, str, bool]] = []

        for name, version in packages:
            if not check_local_git_repo(name):
                sys.exit(1)

            if not version:
                if v := active_git_branch(name):
                    version = v

            info = manager.info(name, version=version, prefer_installed=False)

            if info.invalid_reason:
                UI.error(f'invalid package "{name}": {info.invalid_reason}')
                sys.exit(1)

            if not version:
                version = info.best_version()

            to_validate.append((info.package.qualified_name(), version))
            packages_to_bundle.append(
                (
                    info.package.qualified_name(),
                    info.package.git_url,
                    version,
                    False,
                    False,
                ),
            )

        if not args.nodeps:
            invalid_reason, new_pkgs = manager.validate_dependencies(
                to_validate,
                ignore_installed_packages=True,
                ignore_suggestions=args.nosuggestions,
            )

            if invalid_reason:
                UI.error("failed to resolve dependencies:", invalid_reason)
                sys.exit(1)

        for info, version, suggested in new_pkgs:
            packages_to_bundle.append(
                (
                    info.package.qualified_name(),
                    info.package.git_url,
                    version,
                    True,
                    suggested,
                ),
            )
    else:
        prefer_existing_clones = True

        for ipkg in manager.installed_packages():
            assert ipkg.status.current_version
            packages_to_bundle.append(
                (
                    ipkg.package.qualified_name(),
                    ipkg.package.git_url,
                    ipkg.status.current_version,
                    False,
                    False,
                ),
            )

    if not packages_to_bundle:
        UI.error("no packages to put in bundle")
        sys.exit(1)

    if not args.force:
        package_listing = ""

        for name, git_url, version, is_dependency, is_suggestion in packages_to_bundle:
            package_listing += f"  {name} ({version})"

            if is_suggestion:
                package_listing += " (suggested)"
            elif is_dependency:
                package_listing += " (dependency)"

            if git_url.startswith(BUILTIN_SCHEME):
                package_listing += " (built-in)"

            package_listing += "\n"

        UI.info(f"The following packages will be BUNDLED into {args.bundle_filename}:")
        UI.info(package_listing)

        if not UI.confirmation_prompt("Proceed?"):
            return

    git_urls = [(git_url, version) for _, git_url, version, _, _ in packages_to_bundle]
    error = manager.bundle(
        args.bundle_filename,
        git_urls,
        prefer_existing_clones=prefer_existing_clones,
    )

    if error:
        UI.error("failed to create bundle: {error}")
        sys.exit(1)

    UI.info(f"Bundle successfully written: {args.bundle_filename}")


def cmd_unbundle(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    prev_load_status = {}

    for ipkg in manager.installed_packages():
        prev_load_status[ipkg.package.git_url] = ipkg.status.is_loaded

    if args.replace:
        cmd_purge(manager, args)

    error, bundle_info = manager.bundle_info(args.bundle_filename)

    if error:
        UI.error("failed to unbundle {args.bundle_filename}: {error}")
        sys.exit(1)

    for git_url, _, pkg_info in bundle_info:
        if pkg_info.invalid_reason:
            name = pkg_info.package.qualified_name()
            UI.error(
                f"bundle {args.bundle_filename} contains invalid package {git_url} ({name}): {pkg_info.invalid_reason}",
            )
            sys.exit(1)

    if not bundle_info:
        UI.info("No packages in bundle.")
        return

    if not args.force:
        package_listing = ""
        builtin_listing = ""
        for git_url, version, info in bundle_info:
            name = git_url

            if info.is_builtin():
                builtin_listing += f"  from {name} ({version}):\n"
                continue

            for pkg in manager.source_packages():
                if pkg.git_url == git_url:
                    name = pkg.qualified_name()
                    break

            package_listing += f"  {name} ({version})\n"

        UI.info("The following packages will be INSTALLED:")
        UI.info(package_listing)

        extdep_listing = ""

        for git_url, version, info in bundle_info:
            name = git_url

            for pkg in manager.source_packages():
                if pkg.git_url == git_url:
                    name = pkg.qualified_name()
                    break

            extdeps = info.dependencies(field="external_depends")

            if extdeps is None:
                extdep_listing += f"  from {name} ({version}):\n    <malformed>\n"
                continue

            if extdeps:
                extdep_listing += f"  from {name} ({version}):\n"

                for extdep, semver in sorted(extdeps.items()):
                    extdep_listing += f"    {extdep} {semver}\n"

        if extdep_listing:
            UI.info(
                "Verify the following REQUIRED external dependencies:\n"
                "(Ensure their installation on all relevant systems before"
                " proceeding):",
            )
            UI.info(extdep_listing)

        if not UI.confirmation_prompt("Proceed?"):
            return

    prompt_for_user_vars(
        manager,
        args,
        [info for _, _, info in bundle_info],
    )

    error = manager.unbundle(args.bundle_filename)

    if error:
        UI.error("failed to unbundle {args.bundle_filename}: {error}")
        sys.exit(1)

    for git_url, _, _ in bundle_info:
        if git_url in prev_load_status:
            need_load = prev_load_status[git_url]
        else:
            need_load = True

        ipkg2 = manager.find_installed_package(git_url)

        if not ipkg2:
            UI.info(f'Skipped loading "{git_url}": failed to install')
            continue

        name = ipkg2.package.qualified_name()

        if not need_load:
            UI.info(f'Skipped loading "{name}"')
            continue

        load_error = manager.load(name)

        if load_error:
            UI.info(f'Failed loading "{name}": {load_error}')
        else:
            UI.info(f'Loaded "{name}"')

    UI.info("Unbundling complete.")


def cmd_remove(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    packages_to_remove: list[InstalledPackage] = []

    def package_will_be_removed(pkg_name: str) -> bool:
        for _ipkg in packages_to_remove:
            if _ipkg.package.name == pkg_name:
                return True

        return False

    for name in args.package:
        ipkg = manager.find_installed_package(name)

        if not ipkg:
            UI.error(f'package "{name}" is not installed')
            sys.exit(1)

        if ipkg.is_builtin():
            UI.info(f'cannot remove "{name}": built-in package')
            sys.exit(1)

        packages_to_remove.append(ipkg)

    dependers_to_unload = set()

    if not args.nodeps:
        for ipkg in packages_to_remove:
            for pkg_name in manager.list_depender_pkgs(ipkg.package.name):
                ipkg = manager.find_installed_package(pkg_name)

                if ipkg and not package_will_be_removed(ipkg.package.name):
                    if ipkg.status.is_loaded:
                        dependers_to_unload.add(ipkg.package.name)

    if not args.force:
        UI.info("The following packages will be REMOVED:")

        for ipkg in packages_to_remove:
            UI.info(f"  {ipkg.package.qualified_name()}")

        UI.info()

        if dependers_to_unload:
            UI.info("The following dependent packages will be UNLOADED:")

            for pkg_name in sorted(dependers_to_unload):
                ipkg = manager.find_installed_package(pkg_name)
                assert ipkg
                UI.info(f"  {ipkg.package.qualified_name()}")

            UI.info()

        if not UI.confirmation_prompt("Proceed?"):
            return

    for pkg_name in sorted(dependers_to_unload):
        ipkg = manager.find_installed_package(pkg_name)
        assert ipkg
        name = ipkg.package.qualified_name()

        if manager.unload(name):
            UI.info(f'Unloaded "{name}"')
        else:
            # Weird that it failed, but if it's not installed and there's
            # nothing to unload, not worth using a non-zero exit-code to
            # reflect an overall failure of the package removal operation
            UI.info(f'Failed unloading "{name}": no such package installed')

    had_failure = False

    for ipkg in packages_to_remove:
        name = ipkg.package.qualified_name()
        modifications = manager.modified_config_files(ipkg)
        backup_files = manager.backup_modified_files(name, modifications)

        if manager.remove(name):
            UI.info(f'Removed "{name}"')

            if backup_files:
                UI.info("\tCreated backups of locally modified config files:")

                for backup_file in backup_files:
                    UI.info("\t" + backup_file)

        else:
            UI.info(f'Failed removing "{name}": no such package installed')
            had_failure = True

    if had_failure:
        sys.exit(1)


def cmd_purge(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    packages_to_remove = manager.installed_packages()
    packages_to_remove = [p for p in packages_to_remove if not p.is_builtin()]

    if not packages_to_remove:
        UI.info("No packages to remove.")
        return

    if not args.force:
        package_listing = ""
        names_to_remove = [ipkg.package.qualified_name() for ipkg in packages_to_remove]

        for name in names_to_remove:
            package_listing += f"  {name}\n"

        UI.info("The following packages will be REMOVED:")
        UI.info(package_listing)

        if not UI.confirmation_prompt("Proceed?"):
            return

    had_failure = False

    for ipkg in packages_to_remove:
        name = ipkg.package.qualified_name()
        modifications = manager.modified_config_files(ipkg)
        backup_files = manager.backup_modified_files(name, modifications)

        if manager.remove(name):
            UI.info(f'Removed "{name}"')

            if backup_files:
                UI.info("\tCreated backups of locally modified config files:")

                for backup_file in backup_files:
                    UI.info("\t" + backup_file)

        else:
            UI.info(f'Unknown error removing "{name}"')
            had_failure = True

    if had_failure:
        sys.exit(1)


def outdated(manager: Manager) -> list[str]:
    return [
        ipkg.package.qualified_name()
        for ipkg in manager.installed_packages()
        if ipkg.status.is_outdated
    ]


def cmd_refresh(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    if args.fail_on_aggregate_problems and not args.aggregate:
        UI.warning(
            "--fail-on-aggregate-problems without --aggregate has no effect.",
        )

    if args.push and not args.aggregate:
        UI.error("--push requires --aggregate.")
        sys.exit(1)

    if not args.sources:
        args.sources = list(manager.sources.keys())

    had_failure = False
    had_aggregation_failure = False

    for source in args.sources:
        UI.info(f"Refresh package source: {source}")

        src_pkgs_before = {i.qualified_name() for i in manager.source_packages()}

        error = ""
        aggregation_issues = []

        if args.aggregate:
            res = manager.refresh_source(source)
            if res:
                error = res
            else:
                aggres = manager.aggregate_source(source, args.push)
                error = aggres.error
                aggregation_issues = aggres.package_issues
        else:
            error = manager.refresh_source(source)

        if error:
            had_failure = True
            UI.error(f'failed to refresh "{source}": {error}')
            continue

        src_pkgs_after = {i.qualified_name() for i in manager.source_packages()}

        if src_pkgs_before == src_pkgs_after:
            UI.info("\tNo membership changes")
        else:
            UI.info("\tChanges:")
            diff = src_pkgs_before.symmetric_difference(src_pkgs_after)

            for name in diff:
                change = "Added" if name in src_pkgs_after else "Removed"
                UI.info(f"\t\t{change} {name}")

        if args.aggregate:
            if aggregation_issues:
                UI.info(
                    "\tWARNING: Metadata aggregated, but excludes the "
                    "following packages due to described problems:",
                )

                for url, issue in aggregation_issues:
                    UI.info(f"\t\t{url}: {issue}")
                if args.fail_on_aggregate_problems:
                    had_aggregation_failure = True
            else:
                UI.info("\tMetadata aggregated")

        if args.push:
            UI.info("\tPushed aggregated metadata")

    outdated_before = set(outdated(manager))
    UI.info("Refresh installed packages")
    manager.refresh_installed_packages()
    outdated_after = set(outdated(manager))

    if outdated_before == outdated_after:
        UI.info("\tNo new outdated packages")
    else:
        UI.info("\tNew outdated packages:")
        diff = outdated_before.symmetric_difference(outdated_after)

        for name in diff:
            ipkg = manager.find_installed_package(name)
            if not ipkg:
                continue
            version_change = version_change_string(manager, ipkg)
            UI.info(f"\t\t{name} {version_change}")

    if had_failure:
        sys.exit(1)
    if had_aggregation_failure:
        sys.exit(2)


def version_change_string(
    manager: Manager,
    installed_package: InstalledPackage,
) -> str:
    old_version = installed_package.status.current_version
    new_version = old_version
    version_change = ""

    if installed_package.status.tracking_method == TRACKING_METHOD_VERSION:
        versions = manager.package_versions(installed_package)

        if len(versions):
            new_version = versions[-1]

        version_change = f"({old_version} -> {new_version})"
    else:
        version_change = f"({new_version})"

    return version_change


def cmd_upgrade(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    if args.package:
        pkg_list = args.package
    else:
        pkg_list = outdated(manager)

    outdated_packages = []
    package_listing = ""

    for name in pkg_list:
        ipkg = manager.find_installed_package(name)

        if not ipkg:
            UI.error(f'package "{name}" is not installed')
            sys.exit(1)

        name = ipkg.package.qualified_name()

        if not ipkg.status.is_outdated:
            continue

        if not manager.match_source_packages(name):
            name = ipkg.package.git_url

        info = manager.info(
            name,
            version=ipkg.status.current_version,
            prefer_installed=False,
        )

        if info.invalid_reason:
            UI.error(f'invalid package "{name}": {info.invalid_reason}')
            sys.exit(1)

        next_version = ipkg.status.current_version

        if ipkg.status.tracking_method == TRACKING_METHOD_VERSION and info.versions:
            next_version = info.versions[-1]

        assert next_version

        outdated_packages.append((info, next_version, False))
        version_change = version_change_string(manager, ipkg)
        package_listing += f"  {name} {version_change}\n"

    if not outdated_packages:
        UI.info("All packages already up-to-date.")
        return

    new_pkgs: list[tuple[PackageInfo, str, bool]] = []

    if not args.nodeps:
        to_validate = [
            (info.package.qualified_name(), next_version)
            for info, next_version, _ in outdated_packages
        ]
        invalid_reason, new_pkgs = manager.validate_dependencies(
            to_validate,
            ignore_suggestions=args.nosuggestions,
        )

        if invalid_reason:
            UI.error("failed to resolve dependencies:", invalid_reason)
            sys.exit(1)

    allpkgs = outdated_packages + new_pkgs

    if not args.force:
        UI.info("The following packages will be UPGRADED:")
        UI.info(package_listing)

        if new_pkgs:
            dependency_listing = ""

            for info, version, suggestion in new_pkgs:
                name = info.package.qualified_name()
                dependency_listing += f"  {name} ({version})"

                if suggestion:
                    dependency_listing += " (suggested)"

                dependency_listing += "\n"

            UI.info("The following dependencies will be INSTALLED:")
            UI.info(dependency_listing)

        extdep_listing = ""

        for info, version2, _ in allpkgs:
            name = info.package.qualified_name()
            extdeps = info.dependencies(field="external_depends")

            if extdeps is None:
                extdep_listing += f"  from {name} ({version2}):\n    <malformed>\n"
                continue

            if extdeps:
                extdep_listing += f"  from {name} ({version2}):\n"

                for extdep, semver in sorted(extdeps.items()):
                    extdep_listing += f"    {extdep} {semver}\n"

        if extdep_listing:
            UI.info(
                "Verify the following REQUIRED external dependencies:\n"
                "(Ensure their installation on all relevant systems before"
                " proceeding):",
            )
            UI.info(extdep_listing)

        if not UI.confirmation_prompt("Proceed?"):
            return

    prompt_for_user_vars(
        manager,
        args,
        [info for info, _, _ in allpkgs],
    )

    if not args.skiptests:
        for info, version3, _ in outdated_packages:
            name = info.package.qualified_name()

            # Get info object for the next version as there may have been a
            # test_command added during the upgrade.
            next_info = manager.info(name, version=version3, prefer_installed=False)
            if next_info.invalid_reason:
                UI.error(
                    f'invalid package "{name}": {next_info.invalid_reason}',
                )
                sys.exit(1)

            if "test_command" not in next_info.metadata:
                LOG.info(
                    'Skipping unit tests for "%s": no test_command in metadata',
                    name,
                )
                continue

            UI.info(f'Running unit tests for "{name}"')
            error_msg = ""
            # As in cmd_install, we always process dependencies since the tests
            # might well fail without them. If the user wants --nodeps and the
            # tests fail because of it, they'll also need to say --skiptests.
            error, passed, test_dir = manager.test(
                name,
                version3,
                test_dependencies=True,
            )

            if error:
                error_msg = f"failed to run tests for {name}: {error}"
            elif not passed:
                error_msg = (
                    f'"{name}" tests failed, inspect'
                    f" {manager.package_test_log(info.package.name)} and"
                    f" the contents of {test_dir}"
                )

            if error_msg:
                UI.error(error_msg)

                if args.force:
                    sys.exit(1)

                if not UI.confirmation_prompt(
                    "Proceed to install anyway?",
                    default_to_yes=False,
                ):
                    return

    for info, version, _ in reversed(new_pkgs):
        name = info.package.qualified_name()

        UI.info(f'Installing "{name}"', flush=True)
        # Use default arguments here to avoid late-binding closure:
        err = UI.call_activity(lambda n=name, v=version: manager.install(n, v))

        if err:
            UI.info(f'Failed installing "{name}": {err}')
            continue

        ipkg = manager.find_installed_package(name)
        assert ipkg
        UI.info(f'Installed "{name}" ({ipkg.status.current_version})')

        if manager.has_scripts(ipkg):
            load_error = manager.load(name)

            if load_error:
                UI.info(f'Failed loading "{name}": {load_error}')
            else:
                UI.info(f'Loaded "{name}"')

    had_failure = False

    for info, _, _ in outdated_packages:
        name = info.package.qualified_name()

        if not manager.match_source_packages(name):
            name = info.package.git_url

        ipkg = manager.find_installed_package(name)
        assert ipkg
        modifications = manager.modified_config_files(ipkg)
        backup_files = manager.backup_modified_files(name, modifications)
        prev_upstream_config_files = manager.save_temporary_config_files(ipkg)

        res = manager.upgrade(name)

        if res:
            UI.info(f'Failed upgrading "{name}": {res}')
            had_failure = True
        else:
            ipkg = manager.find_installed_package(name)
            assert ipkg
            UI.info(f'Upgraded "{name}" ({ipkg.status.current_version})')

        for i, mf in enumerate(modifications):
            next_upstream_config_file = mf[1]

            if not os.path.isfile(next_upstream_config_file):
                UI.info("\tConfig file no longer exists:")
                UI.info("\t\t" + next_upstream_config_file)
                UI.info("\tPrevious, locally modified version backed up to:")
                UI.info("\t\t" + backup_files[i])
                continue

            prev_upstream_config_file = prev_upstream_config_files[i][1]

            if filecmp.cmp(prev_upstream_config_file, next_upstream_config_file):
                # Safe to restore user's version
                shutil.copy2(backup_files[i], next_upstream_config_file)
                continue

            UI.info("\tConfig file has been updated to a newer version:")
            UI.info("\t\t" + next_upstream_config_file)
            UI.info("\tPrevious, locally modified version backed up to:")
            UI.info("\t\t" + backup_files[i])

    if had_failure:
        sys.exit(1)


def cmd_load(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    had_failure = False
    load_error = False
    dep_error_listing = ""

    for name in args.package:
        ipkg = manager.find_installed_package(name)

        if not ipkg:
            had_failure = True
            UI.info(f'Failed to load "{name}": no such package installed')
            continue

        if not manager.has_scripts(ipkg):
            UI.info(f'The package "{name}" does not contain scripts to load.')
            continue

        name = ipkg.package.qualified_name()

        saved_state = {}

        if args.nodeps:
            load_error = bool(manager.load(name))
        else:
            saved_state = manager.loaded_package_states()
            dep_error_listing, load_error = "", False

            loaded_dep_list = manager.load_with_dependencies(
                name_from_path(name),
            )

            for _name, _error in loaded_dep_list:
                if _error:
                    load_error = True
                    dep_error_listing += f"  {_name}: {_error}\n"

            if not load_error:
                dep_listing = get_changed_state(manager, saved_state, [name])

                if dep_listing:
                    UI.info(
                        "The following installed packages were additionally loaded to satisfy"
                        f' runtime dependencies for "{name}".',
                    )
                    UI.info(dep_listing)

        if load_error:
            had_failure = True

            if not args.nodeps:
                if dep_error_listing:
                    UI.info(
                        f'The following installed dependencies could not be loaded for "{name}".',
                    )
                    UI.info(dep_error_listing)
                    manager.restore_loaded_package_states(saved_state)

            UI.info(f'Failed to load "{name}": {load_error}')
        else:
            UI.info(f'Loaded "{name}"')

    if had_failure:
        sys.exit(1)


def cmd_unload(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    had_failure = False
    packages_to_unload: list[InstalledPackage] = []
    dependers_to_unload = set()

    def package_will_be_unloaded(pkg_name: str) -> bool:
        for _ipkg in packages_to_unload:
            if _ipkg.package.name == pkg_name:
                return True

        return False

    for name in args.package:
        ipkg = manager.find_installed_package(name)

        if not ipkg:
            had_failure = True
            UI.info(f'Failed to unload "{name}": no such package installed')
            continue

        if not ipkg.status.is_loaded:
            continue

        # Maybe in the future that's possible, but as of now built-in
        # packages are really built-in plugins and there is not a way
        # to unload them.
        if ipkg.is_builtin():
            UI.info(f'cannot unload "{name}": built-in package')
            sys.exit(1)

        packages_to_unload.append(ipkg)

    if not args.nodeps:
        for ipkg in packages_to_unload:
            for pkg_name in manager.list_depender_pkgs(ipkg.package.name):
                ipkg = manager.find_installed_package(pkg_name)

                if ipkg and not package_will_be_unloaded(ipkg.package.name):
                    if ipkg.status.is_loaded:
                        dependers_to_unload.add(ipkg.package.name)

    if packages_to_unload and not args.force:
        UI.info("The following packages will be UNLOADED:")

        for ipkg in packages_to_unload:
            UI.info(f"  {ipkg.package.qualified_name()}")

        UI.info()

        if dependers_to_unload:
            UI.info("The following dependent packages will be UNLOADED:")

            for pkg_name in sorted(dependers_to_unload):
                ipkg = manager.find_installed_package(pkg_name)
                assert ipkg
                UI.info(f"  {ipkg.package.qualified_name()}")

            UI.info()

        if not UI.confirmation_prompt("Proceed?"):
            if had_failure:
                sys.exit(1)
            else:
                return

    for pkg_name in sorted(dependers_to_unload):
        ipkg = manager.find_installed_package(pkg_name)
        assert ipkg
        packages_to_unload.append(ipkg)

    for ipkg in packages_to_unload:
        name = ipkg.package.qualified_name()

        if manager.unload(name):
            UI.info(f'Unloaded "{name}"')
        else:
            had_failure = True
            UI.info(f'Failed unloading "{name}": no such package installed')

    if had_failure:
        sys.exit(1)


def cmd_pin(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    had_failure = False

    for name in args.package:
        ipkg = manager.find_installed_package(name)

        if not ipkg:
            had_failure = True
            UI.info(f'Failed to pin "{name}": no such package installed')
            continue

        if ipkg.is_builtin():
            had_failure = True
            UI.info(f'cannot pin "{name}": built-in package')
            continue

        name = ipkg.package.qualified_name()
        ipkg = manager.pin(name)

        if ipkg:
            UI.info(
                f'Pinned "{name}" at version: {ipkg.status.current_version} ({ipkg.status.current_hash})',
            )
        else:
            had_failure = True
            UI.info(f'Failed pinning "{name}": no such package installed')

    if had_failure:
        sys.exit(1)


def cmd_unpin(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    had_failure = False

    for name in args.package:
        ipkg = manager.find_installed_package(name)

        if not ipkg:
            had_failure = True
            UI.info(f'Failed to unpin "{name}": no such package installed')
            continue

        if ipkg.is_builtin():
            had_failure = True
            UI.info(f'cannot unpin "{name}": built-in package')
            continue

        name = ipkg.package.qualified_name()
        ipkg = manager.unpin(name)

        if ipkg:
            UI.info(
                f'Unpinned "{name}" from version: {ipkg.status.current_version} ({ipkg.status.current_hash})',
            )
        else:
            had_failure = True
            UI.info(f'Failed unpinning "{name}": no such package installed')

    if had_failure:
        sys.exit(1)


def _get_filtered_packages(
    manager: Manager,
    category: str,
) -> (
    dict[str, Package]
    | dict[str, InstalledPackage]
    | dict[str, Package | InstalledPackage]
):
    pkg_dict: dict[str, Package | InstalledPackage] = {}

    for ipkg in manager.installed_packages():
        pkg_dict[ipkg.package.qualified_name()] = ipkg

    for pkg in manager.source_packages():
        pkg_qn = pkg.qualified_name()

        if pkg_qn not in pkg_dict:
            pkg_dict[pkg_qn] = pkg

    if category == "all":
        filtered_pkgs = pkg_dict
    elif category == "installed":
        filtered_pkgs = {
            key: value
            for key, value in pkg_dict.items()
            if isinstance(value, InstalledPackage)
        }
    elif category == "not_installed":
        filtered_pkgs = {
            key: value
            for key, value in pkg_dict.items()
            if not isinstance(value, InstalledPackage)
        }
    elif category == "loaded":
        filtered_pkgs = {
            key: value
            for key, value in pkg_dict.items()
            if isinstance(value, InstalledPackage) and value.status.is_loaded
        }
    elif category == "unloaded":
        filtered_pkgs = {
            key: value
            for key, value in pkg_dict.items()
            if isinstance(value, InstalledPackage) and not value.status.is_loaded
        }
    elif category == "outdated":
        filtered_pkgs = {
            key: value
            for key, value in pkg_dict.items()
            if isinstance(value, InstalledPackage) and value.status.is_outdated
        }
    else:
        raise NotImplementedError

    return filtered_pkgs


def cmd_list(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    filtered_pkgs = _get_filtered_packages(manager, args.category)

    for pkg_name, val in sorted(filtered_pkgs.items()):
        if isinstance(val, InstalledPackage):
            pkg = val.package

            if val.is_builtin() and not args.include_builtin:
                continue

            out = f"{pkg_name} (installed: {val.status.current_version})"
        else:
            pkg = val
            out = pkg_name

        if not args.nodesc:
            desc = pkg.short_description()

            if desc:
                out += " - " + desc

        UI.info(out)


def cmd_search(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    src_pkgs = manager.source_packages()
    matches = set()

    for search_text in args.search_text:
        if search_text[0] == "/" and search_text[-1] == "/":
            try:
                regex = re.compile(search_text[1:-1])
            except re.error as error:
                UI.info(f"invalid regex: {error}")
                sys.exit(1)
            else:
                for pkg in src_pkgs:
                    if regex.search(pkg.name_with_source_directory()):
                        matches.add(pkg)

                    for tag in pkg.tags():
                        if regex.search(tag):
                            matches.add(pkg)

        else:
            for pkg in src_pkgs:
                name = pkg.name_with_source_directory()
                if name and search_text in name:
                    matches.add(pkg)

                for tag in pkg.tags():
                    if search_text in tag:
                        matches.add(pkg)

    if matches:
        for match in sorted(matches):
            out = match.qualified_name()

            ipkg = manager.find_installed_package(match.qualified_name())

            if ipkg:
                out += f" (installed: {ipkg.status.current_version})"

            desc = match.short_description()

            if desc:
                out += " - " + desc

            UI.info(out)

    else:
        UI.info("no matches")


def cmd_info(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    if args.version and len(args.package) > 1:
        UI.error('"info --version" may only be used for a single package')
        sys.exit(1)

    # Dictionary for storing package info to output as JSON
    pkginfo: dict[str, Any] = {}
    had_invalid_package = False

    if len(args.package) == 1:
        try:
            package_names = []
            for pkg_name, info in _get_filtered_packages(
                manager,
                args.package[0],
            ).items():
                if info.is_builtin() and not args.include_builtin:
                    continue

                package_names.append(pkg_name)
        except NotImplementedError:
            package_names = args.package
    else:
        package_names = args.package

    for name in package_names:
        info2 = manager.info(
            name,
            version=args.version,
            prefer_installed=(not args.nolocal),
            update_submodules=False,
        )

        if info2.package:
            name = info2.package.qualified_name()

        if args.json:
            pkginfo[name] = {}
            pkginfo[name]["metadata"] = {}
        else:
            UI.info(f'"{name}" info:')

        if info2.invalid_reason:
            if args.json:
                pkginfo[name]["invalid"] = info2.invalid_reason
            else:
                UI.info(f"\tinvalid package: {info2.invalid_reason}\n")

            had_invalid_package = True
            continue

        if args.json:
            pkginfo[name]["url"] = info2.package.git_url
            pkginfo[name]["versions"] = info2.versions
        else:
            UI.info(f"\turl: {info2.package.git_url}")
            UI.info(f"\tversions: {info2.versions}")

        if info2.status:
            if args.json:
                pkginfo[name]["install_status"] = {}

                for key, value in sorted(info2.status.__dict__.items()):
                    pkginfo[name]["install_status"][key] = value
            else:
                UI.info("\tinstall status:")

                for key, value in sorted(info2.status.__dict__.items()):
                    UI.info(f"\t\t{key} = {value}")

        if args.json:
            if info2.metadata_file:
                pkginfo[name]["metadata_file"] = info2.metadata_file
            pkginfo[name]["metadata"][info2.metadata_version] = {}
        else:
            if info2.metadata_file:
                UI.info(f"\tmetadata file: {info2.metadata_file}")
            UI.info(f'\tmetadata (from version "{info2.metadata_version}"):')

        if len(info2.metadata) == 0:
            if not args.json:
                UI.info("\t\t<empty metadata file>")
        elif args.json:
            _fill_metadata_version(
                pkginfo[name]["metadata"][info2.metadata_version],
                info2.metadata,
            )
        else:
            for key, value in sorted(info2.metadata.items()):
                value = value.replace("\n", "\n\t\t\t")
                UI.info(f"\t\t{key} = {value}")

        # If --json and --allvers given, check for multiple versions and
        # add the metadata for each version to the pkginfo.
        if args.json and args.allvers:
            for vers in info2.versions:
                # Skip the version that was already processed
                if vers != info2.metadata_version:
                    info3 = manager.info(
                        name,
                        vers,
                        prefer_installed=(not args.nolocal),
                        update_submodules=False,
                    )
                    pkginfo[name]["metadata"][info3.metadata_version] = {}
                    if info3.metadata_file:
                        pkginfo[name]["metadata_file"] = info3.metadata_file
                    _fill_metadata_version(
                        pkginfo[name]["metadata"][info3.metadata_version],
                        info3.metadata,
                    )

        if not args.json:
            UI.info()

    if args.json:
        UI.info(json.dumps(pkginfo, indent=args.jsonpretty, sort_keys=True))

    if had_invalid_package:
        sys.exit(1)


def _fill_metadata_version(
    pkginfo_name_metadata_version: dict[str, Any],
    info_metadata: dict[str, str],
) -> None:
    """Fill a dict with metadata information.

        This helper function is called by cmd_info to fill metadata information
        for a specific package version.

    Args:
        pkginfo_name_metadata_version (dict of str -> dict): Corresponds
            to pkginfo[name]['metadata'][info.metadata_version] in cmd_info.

        info_metadata (dict of str->str): Corresponds to info.metadata
            in cmd_info.

    Side effect:
        New dict entries are added to pkginfo_name_metadata_version.
    """
    for key, value in info_metadata.items():
        if key in {"depends", "suggests"}:
            pkginfo_name_metadata_version[key] = {}
            deps = value.split("\n")

            for i in range(1, len(deps)):
                deplist = deps[i].split(" ")
                pkginfo_name_metadata_version[key][deplist[0]] = deplist[1]
        else:
            pkginfo_name_metadata_version[key] = value


def cmd_config(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    if args.config_param == "all":
        out = io.StringIO()
        CONFIG.write(out)
        UI.info(out.getvalue())
        out.close()
    elif args.config_param == "sources":
        for key, value in CONFIG.items("sources"):
            UI.info(f"{key} = {value}")
    elif args.config_param == "user_vars":
        for key, value in CONFIG.items("user_vars"):
            UI.info(f"{key} = {value}")
    else:
        UI.info(CONFIG.get("paths", args.config_param))


def cmd_autoconfig(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    if args.user:
        configfile = os.path.join(CONFIG.home_config_dir(), "config")
        if CONFIG.save(configfile):
            UI.info(f"Successfully wrote config file to {configfile}")
        return

    configfile = CONFIG.find_configfile(args)
    zeek_config = find_program("zeek-config")

    if not zeek_config:
        UI.error('no "zeek-config" in PATH')
        sys.exit(1)

    cmd = subprocess.Popen(
        [zeek_config, "--site_dir", "--plugin_dir", "--prefix", "--zeek_dist"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
    )

    assert cmd.stdout
    script_dir = read_zeek_config_line(cmd.stdout)
    plugin_dir = read_zeek_config_line(cmd.stdout)
    bin_dir = os.path.join(read_zeek_config_line(cmd.stdout), "bin")
    zeek_dist = read_zeek_config_line(cmd.stdout)

    if configfile:
        config_dir = os.path.dirname(configfile)
    else:
        config_dir = CONFIG.default_config_dir()
        configfile = os.path.join(config_dir, "config")

    make_dir(config_dir)

    do_prompt = os.path.isfile(configfile) and not args.force

    CONFIG.set_interactively("paths", "script_dir", script_dir, do_prompt)
    CONFIG.set_interactively("paths", "plugin_dir", plugin_dir, do_prompt)
    CONFIG.set_interactively("paths", "bin_dir", bin_dir, do_prompt)
    CONFIG.set_interactively("paths", "zeek_dist", zeek_dist, do_prompt)

    CONFIG.save(configfile)

    UI.info(f"Successfully wrote config file to {configfile}")


def cmd_env(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    zeek_config = find_program("zeek-config")
    zeekpath = os.environ.get("ZEEKPATH")
    pluginpath = os.environ.get("ZEEK_PLUGIN_PATH")

    if zeek_config:
        cmd = subprocess.Popen(
            [zeek_config, "--zeekpath", "--plugin_dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
            universal_newlines=True,
        )

        assert cmd.stdout
        line1 = read_zeek_config_line(cmd.stdout)
        line2 = read_zeek_config_line(cmd.stdout)

        if not zeekpath:
            zeekpath = line1

        if not pluginpath:
            pluginpath = line2

    zeekpaths = list(zeekpath.split(":")) if zeekpath else []
    pluginpaths = list(pluginpath.split(":")) if pluginpath else []

    zeekpaths.append(CONFIG.zeek_path())
    pluginpaths.append(CONFIG.zeek_plugin_path())

    def remove_redundant_paths(paths: list[str]) -> list[str]:
        return list(OrderedDict.fromkeys(paths))

    zeekpaths = remove_redundant_paths(zeekpaths)
    pluginpaths = remove_redundant_paths(pluginpaths)

    if os.environ.get("SHELL", "").endswith("csh"):
        UI.info("setenv ZEEKPATH {}".format(":".join(zeekpaths)))
        UI.info("setenv ZEEK_PLUGIN_PATH {}".format(":".join(pluginpaths)))
        UI.info(f"setenv PATH {CONFIG.bin_dir()}:$PATH")
    else:
        UI.info("export ZEEKPATH={}".format(":".join(zeekpaths)))
        UI.info("export ZEEK_PLUGIN_PATH={}".format(":".join(pluginpaths)))
        UI.info(f"export PATH={CONFIG.bin_dir()}:$PATH")


def cmd_create(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    tmplname = (
        args.template
        or CONFIG.get("templates", "default", fallback=None)
        or ZKG_DEFAULT_TEMPLATE
    )
    try:
        tmpl = Template.load(tmplname, args.version)
    except LoadError as error:
        msg = f"problem while loading template {tmplname}: {error}"
        LOG.exception(msg)
        UI.error(msg)
        sys.exit(1)

    try:
        package = tmpl.package()
        uvars = tmpl.define_user_vars()
        uvar_names = set(package.needed_user_vars())

        # Overlay any requested features onto the package.
        if args.features:
            # If the user provided comma-separated values, split the
            # strings. (Argparse expects space separation.) Also
            # filter any duplicates.
            fnames = set()
            for feat in args.features:
                fnames |= {f.strip() for f in feat.split(",") if f}
            features = tmpl.features()
            for feature in features:
                if feature.name() in fnames:
                    package.add_feature(feature)
                    uvar_names |= set(feature.needed_user_vars())
                    fnames.remove(feature.name())
            if len(fnames) > 0:
                # Alert if the user requested an unknown feature.
                knowns = ", ".join([f'"{f.name()}"' for f in features])
                unknowns = ", ".join([f'"{name}"' for name in fnames])
                UI.error(
                    "the following features are unknown: {}."
                    ' Template "{}" offers {}.'.format(
                        unknowns,
                        tmpl.name(),
                        knowns or "no features",
                    ),
                )
                sys.exit(1)

        # Remove user vars we don't actually require from consideration
        uvars = [uvar for uvar in uvars if uvar.name() in uvar_names]

        # Resolve the variables via user input, args, etc
        for uvar in uvars:
            try:
                uvar.resolve(tmpl.name(), args.user_var, args.force)
            except ValueError:
                UI.error(
                    f'could not determine value of user variable "{uvar.name()}",'
                    " provide via environment or --user-var",
                )
                sys.exit(1)

        # Apply them to the template. After this, any parameter can be
        # retrieved from the template via tmpl.lookup_param().
        tmpl._set_user_vars(uvars)

        # Verify that resulting template parameters are formatted correctly
        try:
            package.do_validate(tmpl)
        except InputError as error:
            UI.error("template input invalid, " + str(error))
            sys.exit(1)

        # And finally, instantiate the package.
        try:
            if os.path.isdir(args.packagedir):
                if not args.force:
                    UI.info(f"Package directory {args.packagedir} already exists.")
                    if not UI.confirmation_prompt("Delete?"):
                        sys.exit(1)
                try:
                    delete_path(args.packagedir)
                    LOG.info(
                        "Removed existing package directory %s",
                        args.packagedir,
                    )
                except OSError as err:
                    UI.error(
                        f"could not remove package directory {args.packagedir}: {err}",
                    )
                    sys.exit(1)

            package.do_instantiate(tmpl, args.packagedir, args.force)
        except OutputError as error:
            UI.error("template instantiation failed, " + str(error))
            sys.exit(1)
    except Exception as error:
        msg = f"problem during template instantiation: {error}"
        LOG.exception(msg)
        UI.error(msg)
        sys.exit(1)


def cmd_template_info(
    manager: Manager,
    args: argparse.Namespace,
) -> None:
    tmplname = (
        args.template
        or CONFIG.get("templates", "default", fallback=None)
        or ZKG_DEFAULT_TEMPLATE
    )

    try:
        tmpl = Template.load(tmplname, args.version)
    except LoadError as error:
        msg = f"problem while loading template {tmplname}: {error}"
        LOG.exception(msg)
        UI.error(msg)
        sys.exit(1)

    tmplinfo = tmpl.info()

    if args.json:
        UI.info(json.dumps(tmplinfo, indent=args.jsonpretty, sort_keys=True))
    else:
        UI.info("API version: " + tmplinfo["api_version"])
        UI.info("features: " + ", ".join(tmplinfo["features"]))
        UI.info("origin: " + tmplinfo["origin"])
        UI.info("provides package: " + str(tmplinfo["provides_package"]).lower())
        UI.info("user vars:")
        for uvar_name, uvar_info in tmplinfo["user_vars"].items():
            UI.info(
                "\t{}: {}, {}, used by {}".format(
                    uvar_name,
                    uvar_info["description"],
                    uvar_info["default"] or "no default",
                    ", ".join(uvar_info["used_by"]) or "not used",
                ),
            )
        UI.info("versions: " + ", ".join(tmplinfo["versions"]))


class BundleHelpFormatter(argparse.ArgumentDefaultsHelpFormatter):
    # Workaround for underlying argparse bug: https://bugs.python.org/issue9338
    def _format_args(self, action: argparse.Action, default_metavar: str) -> str:
        rval = super()._format_args(action, default_metavar)

        if action.nargs == argparse.ZERO_OR_MORE:
            rval += " --"
        elif action.nargs == argparse.ONE_OR_MORE:
            rval += " --"

        return rval


def _top_level_parser() -> argparse.ArgumentParser:
    top_parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="A command-line package manager for Zeek.",
        epilog="Environment Variables:\n\n"
        "    ``ZKG_CONFIG_FILE``:\t"
        "Same as ``--configfile`` option, but has less precedence.\n"
        "    ``ZKG_DEFAULT_SOURCE``:\t"
        f"The default package source to use (normally {ZKG_DEFAULT_SOURCE}).\n"
        "    ``ZKG_DEFAULT_TEMPLATE``:\t"
        f"The default package template to use (normally {ZKG_DEFAULT_TEMPLATE}).\n",
    )
    top_parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s " + VERSION,
    )

    group = top_parser.add_mutually_exclusive_group()
    group.add_argument(
        "--configfile",
        metavar="FILE",
        help="Path to Zeek Package Manager config file. Precludes --user.",
    )
    group.add_argument(
        "--user",
        action="store_true",
        help="Store all state in user's home directory. Precludes --configfile.",
    )

    top_parser.add_argument(
        "--verbose",
        "-v",
        action="count",
        default=0,
        help="Increase program output for debugging."
        " Use multiple times for more output (e.g. -vv).",
    )
    top_parser.add_argument(
        "--extra-source",
        action="append",
        metavar="NAME=URL",
        help="Add an extra source.",
    )
    return top_parser


def argparser() -> argparse.ArgumentParser:
    pkg_name_help = (
        "The name(s) of package(s) to operate on.  The package"
        " may be named in several ways.  If the package is part"
        " of a package source, it may be referred to by the"
        " base name of the package (last component of git URL)"
        " or its path within the package source."
        " If two packages in different package sources"
        " have conflicting paths, then the package source"
        " name may be prepended to the package path to resolve"
        " the ambiguity. A full git URL may also be used to refer"
        " to a package that does not belong to a source. E.g. for"
        ' a package source called "zeek" that has a package named'
        ' "foo" located in "alice/zkg.index", the following'
        ' names work: "foo", "alice/foo", "zeek/alice/foo".'
    )

    def add_uservar_args(
        parser: argparse.ArgumentParser,
        force_help: str | None = None,
    ) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help=force_help or "Don't prompt for confirmation or user variables.",
        )
        parser.add_argument(
            "--user-var",
            action="append",
            metavar="NAME=VAL",
            type=UserVar.parse_arg,
            help="A user variable assignment. This avoids prompting"
            " for input and lets you provide a value when using --force."
            " Use repeatedly as needed for multiple values.",
        )

    def add_json_args(parser: argparse.ArgumentParser, help_text: str) -> None:
        parser.add_argument("--json", action="store_true", help=help_text)
        parser.add_argument(
            "--jsonpretty",
            type=int,
            default=None,
            metavar="SPACES",
            help="Optional number of spaces to indent for pretty-printed JSON output.",
        )

    top_parser = _top_level_parser()
    command_parser = top_parser.add_subparsers(
        title="commands",
        dest="command",
        help="See `%(prog)s <command> -h` for per-command usage info.",
    )
    command_parser.required = True

    # test
    sub_parser = command_parser.add_parser(
        "test",
        help="Runs unit tests for Zeek packages.",
        description="Runs the unit tests for the specified Zeek packages."
        ' In most cases, the "zeek" and "zeek-config" programs will'
        " need to be in PATH before running this command.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_test)
    sub_parser.add_argument("package", nargs="+", help=pkg_name_help)
    sub_parser.add_argument(
        "--version",
        default=None,
        help="The version of the package to test.  Only one package may be"
        " specified at a time when using this flag.  A version tag, branch"
        " name, or commit hash may be specified here."
        " If the package name refers to a local git repo with a working tree,"
        " then its currently active branch is used."
        " The default for other cases is to use"
        " the latest version tag, or if a package has none,"
        ' the default branch, like "main" or "master".',
    )

    # install
    sub_parser = command_parser.add_parser(
        "install",
        help="Installs Zeek packages.",
        description="Installs packages from a configured package source or"
        " directly from a git URL.  After installing, the package"
        ' is marked as being "loaded" (see the ``load`` command).',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_install)
    sub_parser.add_argument("package", nargs="+", help=pkg_name_help)
    sub_parser.add_argument(
        "--skiptests",
        action="store_true",
        help="Skip running unit tests for packages before installation.",
    )
    sub_parser.add_argument(
        "--nodeps",
        action="store_true",
        help="Skip all dependency resolution/checks.  Note that using this"
        " option risks putting your installed package collection into a"
        " broken or unusable state.",
    )
    sub_parser.add_argument(
        "--nosuggestions",
        action="store_true",
        help="Skip automatically installing suggested packages.",
    )
    sub_parser.add_argument(
        "--version",
        default=None,
        help="The version of the package to install.  Only one package may be"
        " specified at a time when using this flag.  A version tag, branch"
        " name, or commit hash may be specified here."
        " If the package name refers to a local git repo with a working tree,"
        " then its currently active branch is used."
        " The default for other cases is to use"
        " the latest version tag, or if a package has none,"
        ' the default branch, like "main" or "master".',
    )
    add_uservar_args(sub_parser)

    # bundle
    sub_parser = command_parser.add_parser(
        "bundle",
        help="Creates a bundle file containing a collection of Zeek packages.",
        description="This command creates a bundle file containing a collection"
        " of Zeek packages.  If ``--manifest`` is used, the user"
        " supplies the list of packages to put in the bundle, else"
        " all currently installed packages are put in the bundle."
        " A bundle file can be unpacked on any target system,"
        " resulting in a repeatable/specific set of packages"
        " being installed on that target system (see the"
        " ``unbundle`` command).  This command may be useful for"
        " those that want to manage packages on a system that"
        " otherwise has limited network connectivity.  E.g. one can"
        " use a system with an internet connection to create a"
        " bundle, transport that bundle to the target machine"
        " using whatever means are appropriate, and finally"
        " unbundle/install it on the target machine.",
        formatter_class=BundleHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_bundle)
    sub_parser.add_argument(
        "bundle_filename",
        metavar="filename.bundle",
        help="The path of the bundle file to create.  It will be overwritten"
        " if it already exists.  Note that if --manifest is used before"
        " this filename is specified, you should use a double-dash, --,"
        " to first terminate that argument list.",
    )
    sub_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    sub_parser.add_argument(
        "--nodeps",
        action="store_true",
        help="Skip all dependency resolution/checks.  Note that using this"
        " option risks creating a bundle of packages that is in a"
        " broken or unusable state.",
    )
    sub_parser.add_argument(
        "--nosuggestions",
        action="store_true",
        help="Skip automatically bundling suggested packages.",
    )
    sub_parser.add_argument(
        "--manifest",
        nargs="+",
        help="This may either be a file name or a list of packages to include"
        " in the bundle.  If a file name is supplied, it should be in INI"
        " format with a single ``[bundle]`` section.  The keys in that section"
        " correspond to package names and their values correspond to git"
        " version tags, branch names, or commit hashes.  The values may be"
        " left blank to indicate that the latest available version should be"
        " used.",
    )

    # unbundle
    sub_parser = command_parser.add_parser(
        "unbundle",
        help="Unpacks Zeek packages from a bundle file and installs them.",
        description="This command unpacks a bundle file formerly created by the"
        " ``bundle`` command and installs all the packages"
        " contained within.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_unbundle)
    sub_parser.add_argument(
        "bundle_filename",
        metavar="filename.bundle",
        help="The path of the bundle file to install.",
    )
    sub_parser.add_argument(
        "--replace",
        action="store_true",
        help="Using this flag first removes all installed packages before then"
        " installing the packages from the bundle.",
    )
    add_uservar_args(sub_parser)

    # remove
    sub_parser = command_parser.add_parser(
        "remove",
        aliases=["uninstall"],
        help="Uninstall a package.",
        description="Unloads (see the ``unload`` command) and uninstalls a"
        " previously installed package.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_remove)
    sub_parser.add_argument("package", nargs="+", help=pkg_name_help)
    sub_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    sub_parser.add_argument(
        "--nodeps",
        action="store_true",
        help="Skip all dependency resolution/checks.  Note that using this"
        " option risks putting your installed package collection into a"
        " broken or unusable state.",
    )

    # purge
    sub_parser = command_parser.add_parser(
        "purge",
        help="Uninstall all packages.",
        description="Unloads (see the ``unload`` command) and uninstalls all"
        " previously installed packages.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_purge)
    sub_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt.",
    )

    # refresh
    sub_parser = command_parser.add_parser(
        "refresh",
        help="Retrieve updated package metadata.",
        description="Retrieve latest package metadata from sources and checks"
        " whether any installed packages have available upgrades."
        " Note that this does not actually upgrade any packages (see the"
        " ``upgrade`` command for that).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_refresh)
    sub_parser.add_argument(
        "--aggregate",
        action="store_true",
        help="Crawls the urls listed in package source zkg.index files and"
        " aggregates the metadata found in their zkg.meta (or legacy"
        " bro-pkg.meta) files.  The aggregated metadata is stored in the local"
        " clone of the package source that zkg uses internally for locating"
        " package metadata."
        " For each package, the metadata is taken from the highest available"
        ' git version tag or the default branch, like "main" or "master", if no version tags exist',
    )
    sub_parser.add_argument(
        "--fail-on-aggregate-problems",
        action="store_true",
        help="When using --aggregate, exit with error when any packages trigger"
        " metadata problems. Normally such problems only cause a warning.",
    )
    sub_parser.add_argument(
        "--push",
        action="store_true",
        help="Push local package source aggregations to upstream repos. Requires --aggregate.",
    )
    sub_parser.add_argument(
        "--sources",
        nargs="+",
        help="A list of package source names to operate on.  If this argument"
        " is not used, then the command will operate on all configured"
        " sources.",
    )

    # upgrade
    sub_parser = command_parser.add_parser(
        "upgrade",
        help="Upgrade installed packages to latest versions.",
        description="Uprades the specified package(s) to latest available"
        " version.  If no specific packages are specified, then all installed"
        " packages that are outdated and not pinned are upgraded.  For packages"
        " that are installed with ``--version`` using a git branch name, the"
        " package is updated to the latest commit on that branch, else the"
        " package is updated to the highest available git version tag.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_upgrade)
    sub_parser.add_argument("package", nargs="*", default=[], help=pkg_name_help)
    sub_parser.add_argument(
        "--skiptests",
        action="store_true",
        help="Skip running unit tests for packages before installation.",
    )
    sub_parser.add_argument(
        "--nodeps",
        action="store_true",
        help="Skip all dependency resolution/checks.  Note that using this"
        " option risks putting your installed package collection into a"
        " broken or unusable state.",
    )
    sub_parser.add_argument(
        "--nosuggestions",
        action="store_true",
        help="Skip automatically installing suggested packages.",
    )
    add_uservar_args(sub_parser)

    # load
    sub_parser = command_parser.add_parser(
        "load",
        help="Register packages to be be auto-loaded by Zeek.",
        description="The Zeek Package Manager keeps track of all packages that"
        ' are marked as "loaded" and maintains a single Zeek script that, when'
        " loaded by Zeek (e.g. via ``@load packages``), will load the scripts"
        ' from all "loaded" packages at once.'
        ' This command adds a set of packages to the "loaded packages" list.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_load)
    sub_parser.add_argument(
        "package",
        nargs="+",
        default=[],
        help="Name(s) of package(s) to load.",
    )
    sub_parser.add_argument(
        "--nodeps",
        action="store_true",
        help="Skip all dependency resolution/checks.  Note that using this"
        " option risks putting your installed package collection into a"
        " broken or unusable state.",
    )

    # unload
    sub_parser = command_parser.add_parser(
        "unload",
        help="Unregister packages to be be auto-loaded by Zeek.",
        description="The Zeek Package Manager keeps track of all packages that"
        ' are marked as "loaded" and maintains a single Zeek script that, when'
        ' loaded by Zeek, will load the scripts from all "loaded" packages at'
        ' once.  This command removes a set of packages from the "loaded'
        ' packages" list.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_unload)
    sub_parser.add_argument("package", nargs="+", default=[], help=pkg_name_help)
    sub_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt.",
    )
    sub_parser.add_argument(
        "--nodeps",
        action="store_true",
        help="Skip all dependency resolution/checks.  Note that using this"
        " option risks putting your installed package collection into a"
        " broken or unusable state.",
    )

    # pin
    sub_parser = command_parser.add_parser(
        "pin",
        help="Prevent packages from being automatically upgraded.",
        description="Pinned packages are ignored by the ``upgrade`` command.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_pin)
    sub_parser.add_argument("package", nargs="+", default=[], help=pkg_name_help)

    # unpin
    sub_parser = command_parser.add_parser(
        "unpin",
        help="Allows packages to be automatically upgraded.",
        description="Packages that are not pinned are automatically upgraded"
        " by the ``upgrade`` command",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_unpin)
    sub_parser.add_argument("package", nargs="+", default=[], help=pkg_name_help)

    # list
    sub_parser = command_parser.add_parser(
        "list",
        help="Lists packages.",
        description="Outputs a list of packages that match a given category.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_list)
    sub_parser.add_argument(
        "category",
        nargs="?",
        default="installed",
        choices=["all", "installed", "not_installed", "loaded", "unloaded", "outdated"],
        help="Package category used to filter listing.",
    )
    sub_parser.add_argument(
        "--nodesc",
        action="store_true",
        help="Do not display description text, just the package name(s).",
    )
    sub_parser.add_argument(
        "--include-builtin",
        action="store_true",
        help="Also output packages that Zeek has built-in. By default"
        " these are not shown.",
    )

    # search
    sub_parser = command_parser.add_parser(
        "search",
        help="Search packages for matching names.",
        description="Perform a substring search on package names and metadata"
        " tags.  Surround search text with slashes to indicate it is a regular"
        " expression (e.g. ``/text/``).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_search)
    sub_parser.add_argument(
        "search_text",
        nargs="+",
        default=[],
        help="The text(s) or pattern(s) to look for.",
    )

    # info
    sub_parser = command_parser.add_parser(
        "info",
        help="Display package information.",
        description="Shows detailed information/metadata for given packages."
        " If the package is currently installed, additional information about"
        " the status of it is displayed.  E.g. the installed version or whether"
        ' it is currently marked as "pinned" or "loaded."',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_info)
    sub_parser.add_argument(
        "package",
        nargs="+",
        default=[],
        help=pkg_name_help
        + " If a single name is given and matches one of the same categories"
        ' as the "list" command, then it is automatically expanded to be the'
        " names of all packages which match the given category.",
    )
    sub_parser.add_argument(
        "--version",
        default=None,
        help="The version of the package metadata to inspect.  A version tag,"
        " branch name, or commit hash and only one package at a time may be"
        " given when using this flag.  If unspecified, the behavior depends"
        " on whether the package is currently installed.  If installed,"
        " the metadata will be pulled from the installed version.  If not"
        " installed, the latest version tag is used, or if a package has no"
        ' version tags, the default branch, like "main" or "master", is used.',
    )
    sub_parser.add_argument(
        "--nolocal",
        action="store_true",
        help="Do not read information from locally installed packages."
        " Instead read info from remote GitHub.",
    )
    sub_parser.add_argument(
        "--include-builtin",
        action="store_true",
        help="Also output packages that Zeek has built-in. By default"
        " these are not shown.",
    )
    add_json_args(sub_parser, "Output package information as JSON.")
    sub_parser.add_argument(
        "--allvers",
        action="store_true",
        help="When outputting package information as JSON, show metadata for"
        " all versions. This option can be slow since remote repositories"
        " may be cloned multiple times. Also, installed packages will show"
        " metadata only for the installed version unless the --nolocal "
        " option is given.",
    )

    # config
    sub_parser = command_parser.add_parser(
        "config",
        help="Show Zeek Package Manager configuration info.",
        description="The default output of this command is a valid package"
        " manager config file that corresponds to the one currently being used,"
        " but also with any defaulted field values filled in.  This command"
        " also allows for only the value of a specific field to be output if"
        " the name of that field is given as an argument to the command.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_config)
    sub_parser.add_argument(
        "config_param",
        nargs="?",
        default="all",
        choices=[
            "all",
            "sources",
            "user_vars",
            "state_dir",
            "script_dir",
            "plugin_dir",
            "bin_dir",
            "zeek_dist",
        ],
        help="Name of a specific config file field to output.",
    )

    # autoconfig
    sub_parser = command_parser.add_parser(
        "autoconfig",
        help="Generate a Zeek Package Manager configuration file.",
        description="The output of this command is a valid package manager"
        " config file that is generated by using the ``zeek-config`` script"
        " that is installed along with Zeek.  It is the suggested configuration"
        " to use for most Zeek installations.  For this command to work, the"
        " ``zeek-config`` script must be in ``PATH``,"
        " unless the --user option is given, in which case this creates"
        " a config that does not touch the Zeek installation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_autoconfig)
    sub_parser.add_argument(
        "--force",
        action="store_true",
        help="Skip any confirmation prompt.",
    )

    # env
    sub_parser = command_parser.add_parser(
        "env",
        help="Show the value of environment variables that need to be set for"
        " Zeek to be able to use installed packages.",
        description="This command returns shell commands that, when executed,"
        " will correctly set ``ZEEKPATH`` and ``ZEEK_PLUGIN_PATH`` to use"
        " scripts and plugins from packages installed by the package manager."
        " For this command to function properly, either have the ``zeek-config``"
        " script (installed by zeek) in ``PATH``, or have the ``ZEEKPATH`` and"
        " ``ZEEK_PLUGIN_PATH`` environment variables already set so this command"
        " can append package-specific paths to them.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.set_defaults(run_cmd=cmd_env)

    # create
    sub_parser = command_parser.add_parser(
        "create",
        help="Create a new Zeek package.",
        description="This command creates a new Zeek package in the directory"
        " provided via --packagedir. If this directory exists, zkg will not"
        " modify it unless you provide --force.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub_parser.add_argument(
        "--packagedir",
        metavar="DIR",
        required=True,
        help="Output directory into which to produce the new package. Required.",
    )
    sub_parser.add_argument(
        "--version",
        help="The template version to use.  A version tag, branch name, or"
        " commit hash may be specified here.  If --template refers to a local"
        " git repo with a working tree, then zkg uses it as-is and the version"
        " is ignored.  The default for other cases is to use the latest"
        " version tag, or if a template has none, the default branch, like"
        ' "main" or "master".',
    )
    sub_parser.add_argument(
        "--features",
        nargs="+",
        metavar="FEATURE",
        help="Additional features to include in your package. Use the ``template"
        " info`` command for information about available features.",
    )
    sub_parser.add_argument(
        "--template",
        metavar="URL",
        help="By default, zkg uses its own package template. This makes it"
        " select an alternative.",
    )
    sub_parser.set_defaults(run_cmd=cmd_create)
    add_uservar_args(sub_parser)

    # Template management commands

    sub_parser = command_parser.add_parser("template", help="Manage package templates.")

    template_command_parser = sub_parser.add_subparsers(
        title="template commands",
        dest="command",
        help="See %(prog)s <command> -h for per-command usage info.",
    )
    template_command_parser.required = True

    # template info

    sub_parser = template_command_parser.add_parser(
        "info",
        help="Shows information about a package template.",
        description="This command shows versions and supported features for"
        " a given package.",
    )
    add_json_args(sub_parser, "Output template information as JSON.")
    sub_parser.add_argument(
        "--version",
        help="The template version to report on.  A version tag, branch name,"
        " or commit hash may be specified here.  If the selected template"
        " refers to a local git repo, the version is ignored.  The default"
        " for other cases is to use the latest version tag, or if a template"
        ' has none, the default branch, like "main" or "master".',
    )
    sub_parser.add_argument(
        "template",
        metavar="URL",
        nargs="?",
        help="URL of a package template repository, or local path to one."
        " When not provided, the configured default template is used.",
    )
    sub_parser.set_defaults(run_cmd=cmd_template_info)

    if argcomplete_ := sys.modules.get("argcomplete"):
        argcomplete_.autocomplete(top_parser)

    return top_parser
