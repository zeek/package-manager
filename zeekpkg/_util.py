"""
These are meant to be private utility methods for internal use.
"""

import os
import sys
import errno
import shutil
import git


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
        elif os.path.isfile(path):
            raise


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


def find_sentence_end(s):
    beg = 0

    while True:
        period_idx = s.find('.', beg)

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
            git.Git().clone(git_url, dst_path, '--no-single-branch', recursive=True, depth=1)
        except git.exc.GitCommandError:
            if not git_url.startswith('.') and not git_url.startswith('/'):
                # Not a local repo
                raise

            if not os.path.exists(os.path.join(git_url, '.git', 'shallow')):
                raise

            # Some git versions cannot clone from a shallow-clone, so copy
            # and reset/clean it to a pristine condition.
            copy_over_path(git_url, dst_path)
            rval = git.Repo(dst_path)
            rval.git.reset('--hard')
            rval.git.clean('-ffdx')
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
    rval.git.remote('set-url', 'origin', git_url)
    return rval


def git_checkout(clone, version):
    """Checkout a version of a git repo along with any associated submodules.

    Args:
        clone (git.Repo): the git clone on which to operate

        version (str): the branch, tag, or commit to checkout

    Raises:
        git.exc.GitCommandError: if the git repo is invalid
    """
    clone.git.checkout(version)
    clone.git.submodule('sync', '--recursive')
    clone.git.submodule('update', '--recursive', '--init')


def is_sha1(s):
    if not s:
        return False;

    if len(s) != 40:
        return False

    for c in s:
        if c not in {'a', 'b', 'c', 'd', 'e', 'f',
                     '0', '1', '2', '3', '4', '5', '6', '7', '8', '9'}:
            return False

    return True


def is_exe(path):
    return os.path.isfile(path) and os.access(path, os.X_OK)


def find_program(prog_name):
    path, _ = os.path.split(prog_name)

    if path:
        return prog_name if is_exe(prog_name) else ''

    for path in os.environ["PATH"].split(os.pathsep):
        path = os.path.join(path.strip('"'), prog_name)

        if is_exe(path):
            return path

    return ''

def std_encoding(stream):
    if stream.encoding:
        return stream.encoding

    import locale

    if locale.getdefaultlocale()[1] is None:
        return 'utf-8'

    return locale.getpreferredencoding()

def read_zeek_config_line(stdout):
    rval = stdout.readline()

    # Python 2 returned bytes, Python 3 returned unicode
    if isinstance(rval, bytes):
        rval = rval.decode(std_encoding(sys.stdout))

    return rval.strip()


def get_zeek_version():
    zeek_config = find_program('zeek-config')

    if not zeek_config:
        zeek_config = find_program('bro-config')

    if not zeek_config:
        return ''

    import subprocess
    cmd = subprocess.Popen([zeek_config, '--version'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           bufsize=1, universal_newlines=True)

    return read_zeek_config_line(cmd.stdout)
