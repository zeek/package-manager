"""
These are meant to be private utility methods for internal use.
"""

import errno
import importlib.machinery
import os
import shutil
import string
import tarfile
import types

import git
import semantic_version as semver


def make_dir(path):
    """Create a directory or do nothing if it already exists.

    Raises:
        OSError: if directory cannot be created
    """
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise

        if os.path.isfile(path):
            raise


def normalize_version_tag(tag):
    """Given version string "vX.Y.Z", returns "X.Y.Z".
    Returns other input strings unchanged.
    """
    if len(tag) > 1 and tag[0] == "v" and tag[1].isdigit():
        return tag[1:]

    return tag


def delete_path(path):
    if os.path.islink(path):
        os.remove(path)
        return

    if not os.path.exists(path):
        return

    if os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def copy_over_path(src, dst, ignore=None):
    delete_path(dst)
    shutil.copytree(src, dst, symlinks=True, ignore=ignore)


def make_symlink(target_path, link_path, force=True):
    try:
        os.symlink(target_path, link_path)
    except OSError as error:
        if error.errno == errno.EEXIST and force and os.path.islink(link_path):
            os.remove(link_path)
            os.symlink(target_path, link_path)
        else:
            raise error


def safe_tarfile_extractall(tfile, destdir):
    """Wrapper to tarfile.extractall(), checking for path traversal.

    This adds the safeguards the Python docs for tarfile.extractall warn about:

    Never extract archives from untrusted sources without prior inspection. It
    is possible that files are created outside of path, e.g. members that have
    absolute filenames starting with "/" or filenames with two dots "..".

    Args:
        tfile (str): the tar file to extract

        destdir (str): the destination directory into which to place contents

    Raises:
        Exception: if the tarfile would extract outside destdir
    """

    def is_within_directory(directory, target):
        abs_directory = os.path.abspath(directory)
        abs_target = os.path.abspath(target)
        prefix = os.path.commonprefix([abs_directory, abs_target])
        return prefix == abs_directory

    with tarfile.open(tfile) as tar:
        for member in tar.getmembers():
            member_path = os.path.join(destdir, member.name)
            if not is_within_directory(destdir, member_path):
                raise Exception("attempted path traversal in tarfile")

        tar.extractall(destdir)


def find_sentence_end(s):
    beg = 0

    while True:
        period_idx = s.find(".", beg)

        if period_idx == -1:
            return -1

        if period_idx == len(s) - 1:
            return period_idx

        next_char = s[period_idx + 1]

        if next_char.isspace():
            return period_idx

        beg = period_idx + 1


def git_clone(git_url, dst_path, shallow=False):
    if shallow:
        try:
            git.Git().clone(
                git_url,
                dst_path,
                "--no-single-branch",
                recursive=True,
                depth=1,
            )
        except git.GitCommandError:
            if not git_url.startswith(".") and not git_url.startswith("/"):
                # Not a local repo
                raise

            if not os.path.exists(os.path.join(git_url, ".git", "shallow")):
                raise

            # Some git versions cannot clone from a shallow-clone, so copy
            # and reset/clean it to a pristine condition.
            copy_over_path(git_url, dst_path)
            rval = git.Repo(dst_path)
            rval.git.reset("--hard")
            rval.git.clean("-ffdx")
    else:
        git.Git().clone(git_url, dst_path, recursive=True)

    rval = git.Repo(dst_path)

    # This setting of the "origin" remote will be a no-op in most cases, but
    # for some reason, when cloning from a local directory, the clone may
    # inherit the "origin" instead of using the local directory as its new
    # "origin".  This is bad in some cases since we do not want to try
    # fetching from a remote location (e.g.  when unbundling).  This
    # unintended inheritence of "origin" seems to only happen when cloning a
    # local git repo that has submodules ?
    rval.git.remote("set-url", "origin", git_url)
    return rval


def git_checkout(clone, version):
    """Checkout a version of a git repo along with any associated submodules.

    Args:
        clone (git.Repo): the git clone on which to operate

        version (str): the branch, tag, or commit to checkout

    Raises:
        git.GitCommandError: if the git repo is invalid
    """
    clone.git.checkout(version)
    clone.git.submodule("sync", "--recursive")
    clone.git.submodule("update", "--recursive", "--init")


def git_default_branch(repo):
    """Return default branch of a git repo, like 'main' or 'master'.

    If the Git repository has a remote named 'origin', the default branch
    is taken from the value of its HEAD reference (if it has one).

    If the Git repository has no remote named 'origin' or that remote has no
    HEAD, the default branch is selected in this order: 'main' if it exists,
    'master' if it exists, the currently checked out branch if any, else the
    current detached commit.

    Args:
        repo (git.Repo): the git clone on which to operate
    """

    try:
        remote = repo.remote("origin")
    except ValueError:
        remote = None

    if remote:
        # Technically possible that remote has no HEAD, so guard against that.
        try:
            head_ref_name = remote.refs.HEAD.ref.name
        except Exception:
            head_ref_name = None

        if head_ref_name:
            remote_prefix = "origin/"

            if head_ref_name.startswith(remote_prefix):
                return head_ref_name[len(remote_prefix) :]

            return head_ref_name

    ref_names = [ref.name for ref in repo.refs]

    if "main" in ref_names:
        return "main"

    if "master" in ref_names:
        return "master"

    try:
        # See if there's a branch currently checked out
        return repo.head.ref.name
    except TypeError:
        # No branch checked out, return commit hash
        return repo.head.object.hexsha


def git_version_tags(repo):
    """Returns semver-sorted list of version tag strings in the given repo."""
    tags = []

    for tagref in repo.tags:
        tag = str(tagref.name)
        normal_tag = normalize_version_tag(tag)

        try:
            sv = semver.Version.coerce(normal_tag)
        except ValueError:
            # Skip tags that aren't compatible semantic versions.
            continue
        else:
            tags.append((normal_tag, tag, sv))

    return [t[1] for t in sorted(tags, key=lambda e: e[2])]


def git_pull(repo):
    """Does a git pull followed up a submodule update.

    Args:
        clone (git.Repo): the git clone on which to operate

    Raises:
        git.GitCommandError: in case of git trouble
    """
    repo.git.pull()
    repo.git.submodule("sync", "--recursive")
    repo.git.submodule("update", "--recursive", "--init")


def git_remote_urls(repo):
    """Returns a map of remote name -> URL string for configured remotes.

    You'd normally use repo.remotes[n].urls for this, but with old git versions
    (<2.7, e.g. on CentOS 7 at the time of writing) this triggers a "git remote
    show" (without -n) that will query the remotes, which can fail test
    cases. We use the config subsystem to query the URLs directly -- one of the
    fallback mechanisms in GitPython's Remote.urls() implementation.
    """
    remote_details = repo.git.config("--get-regexp", r"remote\..+\.url")
    remotes = {}

    for line in remote_details.split("\n"):
        try:
            remote, url = line.split(maxsplit=1)
            remote = remote.split(".")[1]
            remotes[remote] = url
        except (ValueError, IndexError):
            pass

    return remotes


def is_sha1(s):
    if not s:
        return False

    if len(s) != 40:
        return False

    hexdigits = set(string.hexdigits.lower())
    return all(c in hexdigits for c in s)


def is_exe(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_program(prog_name):
    path, _ = os.path.split(prog_name)

    if path:
        return prog_name if is_exe(prog_name) else ""

    for path in os.environ["PATH"].split(os.pathsep):
        path = os.path.join(path.strip('"'), prog_name)

        if is_exe(path):
            return path

    return ""


class ZeekInfo:
    """
    Helper class holding information about a Zeek installation.
    """

    def __init__(self, *, zeek: str):
        self._zeek = zeek

    @property
    def zeek(self) -> str:
        """Path to zeek executable."""
        if not self._zeek:
            raise LookupError('No "zeek" executable in PATH')
        return self._zeek


_zeek_info = None


def get_zeek_info() -> ZeekInfo:
    global _zeek_info

    if _zeek_info is None:
        _zeek_info = ZeekInfo(
            zeek=find_program("zeek"),
        )

    return _zeek_info


def std_encoding(stream):
    if stream.encoding:
        return stream.encoding

    import locale

    if locale.getdefaultlocale()[1] is None:
        return "utf-8"

    return locale.getpreferredencoding()


def read_zeek_config_line(stdout):
    return stdout.readline().strip()


def get_zeek_version():
    zeek_config = find_program("zeek-config")

    if not zeek_config:
        return ""

    import subprocess

    cmd = subprocess.Popen(
        [zeek_config, "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
        universal_newlines=True,
    )

    return read_zeek_config_line(cmd.stdout)


def load_source(filename):
    """Loads given Python script from disk.

    Args:
        filename (str): name of a Python script file

    Returns:
        types.ModuleType: a module representing the loaded file
    """
    # https://stackoverflow.com/questions/67631/how-to-import-a-module-given-the-full-path
    # We currrently require Python 3.5+, where the following looks sufficient:
    absname = os.path.abspath(filename)
    dirname = os.path.dirname(absname)

    # Naming here is unimportant, since we access members of the new
    # module via the returned instance.
    loader = importlib.machinery.SourceFileLoader("template_" + dirname, absname)
    mod = types.ModuleType(loader.name)
    loader.exec_module(mod)

    return mod


def configparser_section_dict(parser, section):
    """Returns a dict representing a ConfigParser section.

    Args:
        parser (configparser.ConfigParser): a ConfigParser instance

        section (str): the name of a config section

    Returns:
        dict: a dict with key/val entries corresponding to the requested
        section, or an empty dict if the given parser has no such section.
    """
    res = {}

    if not parser.has_section(section):
        return {}

    for key, val in parser.items(section):
        res[key] = val

    return res
