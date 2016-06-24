import os
import errno


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
