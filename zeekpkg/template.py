"""
A module for instantiating different types of Zeek packages.
"""

import abc
import configparser
import os
import re
import shutil
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

import git
import semantic_version as semver

if TYPE_CHECKING:
    from zeekpkg import UserVar

from . import (
    LOG,
    __version__,
)
from ._util import (
    delete_path,
    git_checkout,
    git_clone,
    git_default_branch,
    git_pull,
    git_remote_urls,
    git_version_tags,
    load_source,
    make_dir,
)
from .package import (
    name_from_path,
)

API_VERSION = "1.1.0"


class Error(Exception):
    """Base class for any template-related errors."""


class InputError(Error):
    """Something's amiss in the input arguments for a package."""


class OutputError(Error):
    """Something's going wrong while producing template output."""


class LoadError(Error):
    """Something's going wrong while retrieving a template."""


class GitError(LoadError):
    """There's git trouble while producing template output."""


class Template:
    """Base class for any template.

    Templates need to derive from this class in their toplevel
    __init__.py. Instances of this class pull together the components
    in a given template and capture their parameterization.
    """

    @staticmethod
    def load(
        config: configparser.ConfigParser,
        template: str,
        version: str | None = None,
    ) -> "Template":
        """Template loader.

        This function instantiates a zeekpkg.template.Template
        from a template source present either locally on disk
        or provided as a repo URL.

        It first uses the template's __init__.py to bootstrap a module
        on the fly, then instantiates the zeekpkg.template.Template
        derivative that must be present in it.

        Args:
            config (configparser.ConfigParser): a zkg configuration

            template (str): template source repo, as directory or git URL

            version (str): if provided, a specific version tag to use.
                Ignored when "template" is a local directory. Otherwise,
                the same logic applies as with packages: the most recent
                version tag is picked, and if no version tags are available,
                the default branch.

        Returns:
            zeekpkg.template.Template derivative

        Raises:
            zeekpkg.template.GitError: git hit a problem during cloning/checkout

            zeekpkg.template.LoadError: the template Python code does not load cleanly
        """
        repo = None
        if os.path.isdir(template):
            # We are loading a template from disk. This can be a git
            # repo, in which case we use it as-is. Version requests do
            # not apply. This mirrors the behavior for locally cloned
            # package sources that zkg installs.
            if version is not None:
                LOG.warning('ignoring version request "%s" on local template', version)
            try:
                repo = git.Repo(template)
                if not repo.is_dirty():
                    ref = repo.head.ref
                    assert hasattr(ref, "commit")
                    version = ref.commit.hexsha[:8]
            except git.InvalidGitRepositoryError:
                pass
            templatedir = template
        else:
            # We're loading from a git URL. We'll maintain it in the
            # zkg state folder's clone space and support version
            # requests.
            template_clonedir = os.path.join(
                config.get("paths", "state_dir"),
                "clones",
                "template",
            )
            templatedir = os.path.join(template_clonedir, name_from_path(template))
            make_dir(template_clonedir)

            try:
                if os.path.isdir(templatedir):
                    # A repo of the requested name is already cloned locally.
                    repo = git.Repo(templatedir)
                    # If the requested URL is not the (only) remote of
                    # this repo, treat it like a different repo and
                    # clone anew. (It could be a fork of the same
                    # repo, for example, or simply a naming
                    # collision.)
                    cur_remote_urls = set()
                    for remote in repo.remotes:
                        cur_remote_urls |= set(remote.urls)
                    if len(cur_remote_urls) == 1 and template in cur_remote_urls:
                        repo.git.fetch("-f", "--recurse-submodules=yes", "--tags")
                    else:
                        delete_path(templatedir)
                        repo = None
                if repo is None:
                    repo = git_clone(template, templatedir)
            except git.GitCommandError as error:
                msg = f'failed to update template "{template}": {error}'
                LOG.error(msg)
                raise GitError(msg) from error

            if version is None:
                version_tags = git_version_tags(repo)

                if len(version_tags):
                    version = version_tags[-1]
                else:
                    version = git_default_branch(repo)

            try:
                git_checkout(repo, version)
            except git.GitCommandError as error:
                msg = f'failed to checkout branch/version "{version}" of template {template}: {error}'
                LOG.warning(msg)
                raise GitError(msg) from error

            try:
                # If we're on a branch, pull in latest updates.
                # Pulling fails when on a tag/commit. Accessing the
                # following raises a TypeError when we're not on a
                # branch.
                _ = repo.active_branch
                git_pull(repo)
            except TypeError:
                pass  # Not on a branch, do nothing
            except git.GitCommandError as error:
                msg = f'failed to update branch "{version}" of template {template}: {error}'
                LOG.warning(msg)
                raise GitError(msg) from error

        try:
            mod = load_source(os.path.join(templatedir, "__init__.py"))
        except Exception as error:
            msg = f'failed to load template "{template}": {error}'
            LOG.exception(msg)
            raise LoadError(msg) from error

        if not hasattr(mod, "TEMPLATE_API_VERSION"):
            msg = "template{} does not indicate its API version".format(
                " version " + version if version else "",
            )
            LOG.error(msg)
            raise LoadError(msg)

        # The above guards against absence of TEMPLATE_API_VERSION, so
        # appease pylint for the rest of this function while we access
        # it.

        try:
            is_compat = Template.is_api_compatible(mod.TEMPLATE_API_VERSION)
        except ValueError:
            raise LoadError(
                f'API version string "{mod.TEMPLATE_API_VERSION}" is invalid',
            ) from None

        if not is_compat:
            msg = "template{} API version is incompatible with zkg ({} vs {})".format(
                " version " + version if version else "",
                mod.TEMPLATE_API_VERSION,
                API_VERSION,
            )
            LOG.error(msg)
            raise LoadError(msg)

        instance: Template = mod.Template(
            templatedir,
            mod.TEMPLATE_API_VERSION,
            version,
            repo,
        )
        return instance

    @staticmethod
    def is_api_compatible(tmpl_ver: str) -> bool:
        """Validate template API compatibility.

        Given a semver string describing the API version for which a
        template was written, verifies that we are compatible with it
        according to semantic versioning rules:

        MAJOR version changes when we make incompatible API changes
        MINOR version changes when you add backwards-compatible functionality
        PATCH version changes when you make backwards-compatible bug fixes.

        Returns:
            bool indicating whether template is comatible.

        Raises:
            ValueError when given version isn't semver-parseable
        """
        tmpl_sv = semver.Version(tmpl_ver)
        api_sv = semver.Version(API_VERSION)

        # A different major version is incompatible by definition
        if tmpl_sv.major != api_sv.major:
            return False

        # Minor version of template can be no larger than ours.
        if tmpl_sv.minor > api_sv.minor:
            return False

        # Patch level does not matter. If ours is less than the
        # template's we're buggy, but the difference doesn't affect API.
        return True

    def __init__(
        self,
        templatedir: str,
        api_version: str,
        version: str | None = None,
        repo: git.Repo | None = None,
    ) -> None:
        """Creates a template.

        Template objects start from a local directory, and potentially
        have a version specified. They support the definition and
        lookup of parameters required during the creation of package
        instances from the template. They derive these parameters from
        user variables the template provides and that zkg prompts for
        upon instantiation.

        Args:
            templatedir (str): path to template sources on disk

            api_version (str): API version targeted by the template
                (via TEMPLATE_API_VERSION string)

            version (str): version string of this instance (optional)

            repo (git.Repo): git repo if this template has one (optional)
        """
        self._templatedir = templatedir
        self._api_version = api_version
        self._version = version
        self._repo = repo
        self._params: dict[str, str] = {}  # str -> str, set via self.define_param()
        self._user_vars: list[UserVar] = []

    def define_user_vars(self) -> list["UserVar"]:
        """Defines the full set of user vars supported by this template.

        This function defines the complete set of user vars supported
        by the template content. Instances of zeekpkg.template.Package
        and zeekpkg.template.Feature declare which of these user vars
        they require by implementing the needed_user_vars() method,
        returning the names of those variables.

        The default implementation declares no user variables.

        Returns:
            list of zeekpkg.uservar.Uservar instances
        """
        return []

    @abc.abstractmethod
    def apply_user_vars(self, user_vars: list["UserVar"]) -> None:
        """Apply the user variables to this template.

        Override this by invoking self.define_param() as needed to create
        template parameters based on the provided user vars. The
        relationship of user vars to template parameters is up to
        you. They can be a 1:1 mapping, you can derive additional
        parameters from a single user var (e.g. to accommodate string
        suffixes), or you can use a combination of user vars to define
        a resulting parameter.

        Args:
            user_vars (list of zeekpkg.uservar.UserVar): input values for the template.
        """

    @abc.abstractmethod
    def package(self) -> "Package":
        """Provides a package template to instantiate.

        If the template provides a Zeek package, return a Package
        instance from this method.

        Returns:
            zeekpkg.template.Package instance
        """

    def features(self) -> list["Feature"]:
        """Provides any additional features templates supported.

        If the template provides extra features, return each as an
        instance of zeekpkg.template.Feature instance in a list. By
        default, a template offers no features.

        Returns:
            list of zeekpkg.template.Feature instances
        """
        return []

    def templatedir(self) -> str:
        """Returns the path to the template's source tree on disk."""
        return self._templatedir

    def name(self) -> str:
        """A name for this template, derived from the repo URL."""
        return name_from_path(self._templatedir)

    def api_version(self) -> str:
        """The template API version string declared in this instance's module."""
        return self._api_version

    def version(self) -> str | None:
        """A version string for the template.

        This can be a git tag, branch, commit hash, or None if we're
        using the latest version on the default branch.
        """
        return self._version

    def has_repo(self) -> bool:
        """Returns True if this template has a git repository, False otherwise."""
        return self._repo is not None

    def version_branch(self) -> str | None:
        """Name of the branch the template is on, if any.

        Returns branch name if this template version is a branch HEAD, None
        otherwise (i.e. when it's a specific commit or tag, or we have no
        repository).
        """
        try:
            # The following raises a TypeError when not on a branch
            if self._repo and self._repo.active_branch:
                return self._repo.active_branch.name
        except TypeError:
            pass  # Not on a branch

        return None

    def version_sha(self) -> str | None:
        """The git commit hash for this template's version.

        Returns None when this template got instantiated without a git repo,
        otherwise a string with the full hash value in ASCII.
        """
        try:
            if self._repo:
                ref = self._repo.head.ref
                assert hasattr(ref, "commit")
                return str(ref.commit.hexsha)
        except Exception:
            pass

        return None

    def define_param(self, name: str, val: str) -> None:
        """Defines a parameter of the given name and value."""
        self._params[name] = val

    def lookup_param(self, name: str, default: str = "") -> str:
        """Looks up a parameter, falling back to the given default."""
        return self._params.get(name, default)

    def params(self) -> dict[str, str]:
        """Returns current str->str template parameter dict."""
        return self._params

    def info(self) -> dict[str, Any]:
        """Returns a dict capturing information about this template

        This is usable when rendered as JSON, and also serves as the
        input for our human-readable template information.
        """
        # In the future a template may not provide a full package,
        # only features overlaid in combination with another template.
        res: dict[str, Any] = {
            "api_version": self._api_version,
            "provides_package": False,
        }

        # XXX we should revisit the reported 'origin' value in
        # API 2.0.0 -- the the ad-hoc strings are less helpful
        # than simply providing the key only when there's an
        # actual origin.

        if self._repo is not None:
            try:
                remotes = git_remote_urls(self._repo)
                res["origin"] = remotes["origin"]
            except KeyError:
                res["origin"] = "unavailable"
            res["versions"] = git_version_tags(self._repo)
            res["has_repo"] = True
        else:
            res["origin"] = "not a git repository"
            res["versions"] = []
            res["has_repo"] = False

        pkg = self.package()
        uvars = self.define_user_vars()
        feature_names = []
        res["user_vars"] = {}

        for uvar in uvars:
            res["user_vars"][uvar.name()] = {
                "description": uvar.desc(),
                "default": uvar.default(),
                "used_by": [],
            }

        if pkg is not None:
            res["provides_package"] = True
            for uvar_name in pkg.needed_user_vars():
                try:
                    res["user_vars"][uvar_name]["used_by"].append("package")
                except KeyError:
                    LOG.warning(
                        'Package requires undefined user var "%s", skipping',
                        uvar_name,
                    )

        for feature in self.features():
            feature_names.append(feature.name())
            for uvar_name in feature.needed_user_vars():
                try:
                    res["user_vars"][uvar_name]["used_by"].append(feature.name())
                except KeyError:
                    LOG.warning(
                        'Feature "%s" requires undefined user var "%s"',
                        feature.name(),
                        uvar_name,
                    )

        res["features"] = sorted(feature_names)
        return res

    def _set_user_vars(self, user_vars: list["UserVar"]) -> None:
        """Provides resolved user vars for the template. Used internally."""
        self._params = {}
        self._user_vars = user_vars
        self.apply_user_vars(user_vars)

    def _get_user_vars(self) -> list["UserVar"]:
        """Accessor to resolved user vars. Used internally."""
        return self._user_vars


class _Content(metaclass=abc.ABCMeta):
    """Common functionality for all template content."""

    def __init__(self) -> None:
        self._features: list[Feature] = []
        self._packagedir: str | None = None

    @abc.abstractmethod
    def contentdir(self) -> str:
        """Subdirectory providing this content in the template tree.

        Returns:
            str: relative path to the content directory
        """

    def needed_user_vars(self) -> list[str]:
        """Returns a list of user vars names required by this content.

        Use this function to declare which of the user vars defined by
        the template (via Template.define_user_vars()) is required by
        this component. By doing this, the user only needs to input
        user vars for template components that actually require them.

        Returns:
            A list of strings identifying the needed user vars.
        """
        return []

    def add_feature(self, feature: "Feature") -> None:
        self._features.append(feature)

    def do_validate(self, tmpl: Template) -> None:
        """Main driver for validation of a template's configuration.

        zkg calls this internally as part of template validation.
        You'll likely want to focus on validate() for
        customization.
        """
        self.validate(tmpl)
        for feature in self._features:
            feature.validate(tmpl)

    @abc.abstractmethod
    def validate(self, tmpl: Template) -> None:
        """Validation of template configuration for this component.

        Override this in your template's code in order to check
        whether the template parameters (available via
        tmpl.lookup_param()) are present and correctly
        formatted. Raise zeekpkg.template.InputError exceptions as
        needed.

        Args:
            tmpl (zeekpkg.template.Template): template context

        Raises:
            zeekpkg.template.InputError when failing validation.

        """

    def do_instantiate(
        self,
        tmpl: Template,
        packagedir: str,
        use_force: bool = False,
    ) -> None:
        """Main driver for instantiating template content.

        zkg calls this internally as part of template instantiation.
        You'll likely want to focus on instantiate() for
        customization.

        Args:
            tmpl (zeekpkg.template.Template): template context

            packagedir (str): output folder for the instantiation

            use_force (bool): whether to overwrite/recreate files as needed

        """
        self._packagedir = packagedir

        self.instantiate(tmpl)

        for feature in self._features:
            feature.do_instantiate(tmpl, packagedir, use_force=use_force)

    def instantiate(self, tmpl: Template) -> None:
        """Instantiation of this template component.

        This substitutes parameters in the template material and
        instantiates the result in the output directory.

        Args:
            tmpl (zeekpkg.template.Template): template context
        """
        for orig_file, path_name, file_name, content in self._walk(tmpl):
            if os.path.islink(orig_file):
                self.instantiate_symlink(tmpl, orig_file, path_name, file_name, content)
            else:
                self.instantiate_file(tmpl, orig_file, path_name, file_name, content)

    def instantiate_file(
        self,
        tmpl: Template,
        orig_file: str,
        path_name: str,
        file_name: str,
        content: str | bytes,
    ) -> None:
        """Instantiate a regular file in the template.

        This gets invoked by instantiate() as it traverses the
        template content. Each invocation sees the content after
        parameter substitution in file/directory names and file
        content.

        Directories get handled implicitly via the path_name of any
        files contained in it.

        This implementation writes the content to the output file,
        overwriting any pre-existing output.

        Args:
            tmpl (zeekpkg.template.Template): template context

            orig_file (str): the absolute input file name, e.g. "/path/to/template/@param@.zeek"

            path_name (str): the output directory inside the --packagedir

            file_name (str): the resulting output file name, e.g. "result.zeek"

            content (str or bytes): the resulting content for the file.

        """
        assert self._packagedir
        out_dir = os.path.join(self._packagedir, path_name)
        out_file = os.path.join(out_dir, file_name)
        os.makedirs(out_dir, exist_ok=True)

        try:
            with open(out_file, "wb") as hdl:
                assert isinstance(content, bytes)
                hdl.write(content)
            shutil.copymode(orig_file, out_file)
        except OSError as error:
            LOG.warning('I/O error while instantiating "%s": %s', out_file, error)

    def instantiate_symlink(
        self,
        tmpl: Template,
        orig_file: str,
        path_name: str,
        file_name: str,
        target: str | bytes,
    ) -> None:
        """Instantiate a symbolic link in the template.

        This gets invoked by instantiate() as it traverses the
        template content. Each invocation sees the symlink target
        after parameter substitution.

        Directories get handled implicitly via the path_name of any
        files contained in it.

        This implementation deletes existing files and creates the
        symlink in their place.

        Args:
            tmpl (zeekpkg.template.Template): template context

            orig_file (str): the absolute input file name, e.g. "/path/to/template/@param@.zeek"

            path_name (str): the output directory inside the --packagedir

            file_name (str): the resulting output file name, e.g. "result.zeek"

            target (str): the location the symlink points to.
        """
        assert self._packagedir
        out_dir = os.path.join(self._packagedir, path_name)
        out_file = os.path.join(out_dir, file_name)
        os.makedirs(out_dir, exist_ok=True)

        try:
            delete_path(out_file)
            os.symlink(target, out_file)
        except OSError as error:
            LOG.warning('OS error while creating symlink "%s": %s', out_file, error)

    def _walk(self, tmpl: Template) -> Generator[tuple[str, str, str, str | bytes]]:
        """Generator for instantiating template content.

        This walks over the template source tree, yielding for every
        file a 4-tuple of the input file name, the output directory,
        the output file name in that directory, and the file's
        content. For symlinks, the content is the symlink target,
        with any applicable parameter subtitutions made.

        Args:
            tmpl (zeekpkg.template.Template): template context
        """
        prefix = os.path.join(tmpl.templatedir(), self.contentdir())
        for root, _, files in os.walk(prefix):
            assert isinstance(root, str)
            for fname in files:
                assert isinstance(fname, str)
                in_file = root + os.sep + fname

                # Substitute directory and file names
                out_path = self._replace(tmpl, root[len(prefix) + 1 :])
                assert isinstance(out_path, str)
                out_file = self._replace(tmpl, fname)
                assert isinstance(out_file, str)

                if os.path.islink(in_file):
                    out_content = self._replace(tmpl, os.readlink(in_file))
                else:
                    # Substitute file content.
                    try:
                        with open(in_file, "rb") as hdl:
                            out_content = self._replace(tmpl, hdl.read())
                    except OSError as error:
                        LOG.warning("skipping instantiation of %s: %s", in_file, error)
                        continue

                yield in_file, out_path, out_file, out_content

    def _replace(self, tmpl: Template, content: str | bytes) -> str | bytes:
        """Helper for content substitution.

        Args:
            tmpl (zeekpkg.template.Template): template context

            content (str or bytes): unsubstituted template fodder

        Returns:
            str or bytes after parameter substitution.
        """
        for name, val in tmpl.params().items():
            pat = "@" + name + "@"
            if not isinstance(content, str):
                pat_b = bytes(pat, "utf-8")
                val_b = bytes(val, "utf-8")
                content = re.sub(pat_b, val_b, content, flags=re.IGNORECASE)
            else:
                content = re.sub(pat, val, content, flags=re.IGNORECASE)

        return content


class Package(_Content):
    """Template content for a Zeek package.

    This class fills in package-specific functionality but it still
    abstract. At a minimum, your template's Package derivative needs
    to implement contentdir().
    """

    def do_instantiate(
        self,
        tmpl: Template,
        packagedir: str,
        use_force: bool = False,
    ) -> None:
        self._prepare_packagedir(packagedir)
        super().do_instantiate(tmpl, packagedir, use_force)
        self._git_init(tmpl)

    def _prepare_packagedir(self, packagedir: str) -> None:
        os.makedirs(packagedir, exist_ok=True)

    def _git_init(self, tmpl: Template) -> None:
        """Initialize git repo and commit instantiated content."""
        repo = git.Repo.init(self._packagedir)
        for fname in repo.untracked_files:
            repo.index.add(fname)

        ver_info = tmpl.version()
        ver_sha = tmpl.version_sha()

        if ver_info is None:
            if ver_sha:
                ver_info = "version " + ver_sha[:8]
            else:
                ver_info = "no versioning"
        else:
            ver_info = "version " + ver_info
            if ver_sha:
                ver_info += " (" + ver_sha[:8] + ")"

        tmpl_source = tmpl.name()
        if tmpl.has_repo():
            if (source := tmpl.info()["origin"]) != "unavailable":
                tmpl_source = source

        commit_msg = f"""Initial commit.

zkg {__version__} created this package from template "{tmpl_source}"
using {ver_info}."""

        if self._features:
            features = "\n".join(sorted([f"- {f.name()}" for f in self._features]))
            commit_msg += f"""\n\nFeatures:\n\n{features}"""

        if user_vars := tmpl._get_user_vars():
            set_vars = filter(lambda v: v is not None, user_vars)
            uvars = "\n".join([f"- {v.name()}: {v.val()}" for v in set_vars])

            commit_msg += f"""\n\nVariables:\n\n{uvars}"""

        repo.index.commit(commit_msg)


class Feature(_Content):
    """Features overlay additional functionality onto a package.

    This class fills in feature-specific functionality but it still
    abstract. At a minimum, your template's Feature derivative needs
    to implement contentdir().
    """

    def name(self) -> str:
        """A name for this feature. Defaults to its content directory."""
        return self.contentdir() or "unnamed"
