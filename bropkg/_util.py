"""
These are meant to be private utility methods for internal use.
"""

import os
import errno
import shutil


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


def copy_over_path(src, dst):
    delete_path(dst)
    shutil.copytree(src, dst, symlinks=True)


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
