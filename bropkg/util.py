import os
import errno
import shutil


def make_dir(path):
    """Create a directory or do nothing if it already exists.

    :raise OSError: if directory cannot be created
    """
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise
        elif os.path.isfile(path):
            raise


def remove_trailing_slash(path):
    if path.endswith('/'):
        return path[:-1]

    return path


def delete_path(path):
    if not os.path.exists(path):
        return

    if os.path.islink(path):
        os.remove(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def make_symlink(target_path, link_path, force=True):
    try:
        os.symlink(target_path, link_path)
    except OSError as error:
        if error.errno == errno.EEXIST and force:
            os.remove(link_path)
            os.symlink(target_path, link_path)
        else:
            raise error
