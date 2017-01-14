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


def remove_trailing_slashes(path):
    if path.endswith('/'):
        return remove_trailing_slashes(path[:-1])

    return path


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


def git_clone_shallow(git_url, dst_path):
    git.Git().clone(git_url, dst_path, '--no-single-branch', depth=1)
    rval = git.Repo(dst_path)
    rval.git.fetch(tags=True)
    return rval


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


def stdout_encoding():
    if sys.stdout.encoding:
        return sys.stdout.encoding

    import locale
    return locale.getpreferredencoding()


def read_bro_config_line(stdout):
    rval = stdout.readline()

    # Python 2 returned bytes, Python 3 returned unicode
    if isinstance(rval, bytes):
        rval = rval.decode(stdout_encoding())

    return rval.strip()


def get_bro_version():
    bro_config = find_program('bro-config')

    if not bro_config:
        return ''

    import subprocess
    cmd = subprocess.Popen([bro_config, '--version'],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           bufsize=1, universal_newlines=True)

    return read_bro_config_line(cmd.stdout)
