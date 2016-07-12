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
