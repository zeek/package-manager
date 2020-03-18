"""
A module containing the definition of a "package source": a git repository
containing a collection of :file:`zkg.index` (or legacy :file:`bro-pkg.index`)
files.  These are simple INI files that can describe many Zeek packages.  Each
section of the file names a Zeek package along with the git URL where it is
located and metadata tags that help classify/describe it.
"""

import os
import shutil
import git

try:
    from backports import configparser
except ImportError as err:
    import configparser

from . import LOG
from .package import (
    name_from_path,
    Package
)
from ._util import (
    git_checkout,
    git_clone
)

#: The name of package index files.
INDEX_FILENAME = 'zkg.index'
LEGACY_INDEX_FILENAME = 'bro-pkg.index'
#: The name of the package source file where package metadata gets aggregated.
AGGREGATE_DATA_FILE = 'aggregate.meta'


class Source(object):
    """A Zeek package source.

    This class contains properties of a package source like its name, remote git
    URL, and local git clone.

    Attributes:
        name (str): The name of the source as given by a config file key
            in it's ``[sources]`` section.

        git_url (str): The git URL of the package source.

        clone (git.Repo): The local git clone of the package source.
    """

    def __init__(self, name, clone_path, git_url, version=None):
        """Create a package source.

        Raises:
            git.exc.GitCommandError: if the git repo is invalid
            OSError: if the git repo is invalid and can't be re-initialized
        """
        git_url = os.path.expanduser(git_url)
        self.name = name
        self.git_url = git_url
        self.clone = None

        try:
            self.clone = git.Repo(clone_path)
        except git.exc.NoSuchPathError:
            LOG.debug('creating source clone of "%s" at %s', name, clone_path)
            self.clone = git_clone(git_url, clone_path, shallow=True)
        except git.exc.InvalidGitRepositoryError:
            LOG.debug('deleting invalid source clone of "%s" at %s',
                      name, clone_path)
            shutil.rmtree(clone_path)
            self.clone = git_clone(git_url, clone_path, shallow=True)
        else:
            LOG.debug('found source clone of "%s" at %s', name, clone_path)
            old_url = self.clone.git.config('--local', '--get',
                                            'remote.origin.url')

            if git_url != old_url:
                LOG.debug(
                    'url of source "%s" changed from %s to %s, reclone at %s',
                    name, old_url, git_url, clone_path)
                shutil.rmtree(clone_path)
                self.clone = git_clone(git_url, clone_path, shallow=True)

        # Hmm, maybe this needs to be configurable for people that
        # use differently named master branches...
        git_checkout(self.clone, version or "master")

    def __str__(self):
        return self.git_url

    def __repr__(self):
        return self.git_url

    def package_index_files(self):
        """Return a list of paths to package index files in the source."""
        rval = []
        visited_dirs = set()

        for root, dirs, files in os.walk(self.clone.working_dir,
                                         followlinks=True):
            stat = os.stat(root)
            visited_dirs.add((stat.st_dev, stat.st_ino))
            dirs_to_visit_next = []

            for d in dirs:
                stat = os.stat(os.path.join(root, d))

                if (stat.st_dev, stat.st_ino) not in visited_dirs:
                    dirs_to_visit_next.append(d)

            dirs[:] = dirs_to_visit_next

            try:
                dirs.remove('.git')
            except ValueError:
                pass

            for filename in files:
                if filename == INDEX_FILENAME or filename == LEGACY_INDEX_FILENAME:
                    rval.append(os.path.join(root, filename))

        return sorted(rval)

    def packages(self):
        """Return a list of :class:`.package.Package` in the source."""
        rval = []
        # Use raw parser so no value interpolation takes place.
        parser = configparser.RawConfigParser()
        aggregate_file = os.path.join(
            self.clone.working_dir, AGGREGATE_DATA_FILE)
        parser.read(aggregate_file)

        for index_file in self.package_index_files():
            relative_path = index_file[len(self.clone.working_dir) + 1:]
            directory = os.path.dirname(relative_path)
            lines = []

            with open(index_file) as f:
                lines = [line.rstrip('\n') for line in f]

            for url in lines:
                pkg_name = name_from_path(url)
                agg_key = os.path.join(directory, pkg_name)
                metadata = {}

                if parser.has_section(agg_key):
                    metadata = {key: value for key,
                                value in parser.items(agg_key)}

                package = Package(git_url=url, source=self.name,
                                  directory=directory, metadata=metadata)
                rval.append(package)

        return rval
