"""
A module containing the definition of a "package source": a git repository
containing a collection of :file:`bro-pkg.index` files.  These are simple INI
files that can describe many Bro packages.  Each section of the file names
a Bro package along with the git URL where it is located and metadata tags that
help classify/describe it.
"""

import os
import sys
import shutil
import git

if sys.version_info[0] < 3:
    import ConfigParser as configparser
else:
    import configparser

from . import LOG
from .package import Package

#: The name of package index files.
INDEX_FILENAME = 'bro-pkg.index'


class Source(object):
    """A Bro package source.

    This class contains properties of a package source like its name, remote git
    URL, and local git clone.

    Attributes:
        name (str): The name of the source as given by a config file key
            in it's ``[sources]`` section.

        git_url (str): The git URL of the package source.

        clone (git.Repo): The local git clone of the package source.
    """

    def __init__(self, name, clone_path, git_url):
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
            self.clone = git.Repo.clone_from(git_url, clone_path)
        except git.exc.InvalidGitRepositoryError:
            LOG.debug('deleting invalid source clone of "%s" at %s',
                      name, clone_path)
            shutil.rmtree(clone_path)
            self.clone = git.Repo.clone_from(git_url, clone_path)
        else:
            LOG.debug('found source clone of "%s" at %s', name, clone_path)
            old_url = self.clone.git.config('--local', '--get',
                                            'remote.origin.url')

            if git_url != old_url:
                LOG.debug(
                    'url of source "%s" changed from %s to %s, reclone at %s',
                    name, old_url, git_url, clone_path)
                shutil.rmtree(clone_path)
                self.clone = git.Repo.clone_from(git_url, clone_path)

    def __str__(self):
        return self.git_url

    def __repr__(self):
        return self.git_url

    def package_index_files(self):
        """Return a list paths to package index files in the source."""
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
                if filename == INDEX_FILENAME:
                    rval.append(os.path.join(root, filename))

        return rval

    def packages(self):
        """Return a list of :class:`.package.Package` in the source."""
        rval = []

        for index_file in self.package_index_files():
            relative_path = index_file[len(self.clone.working_dir) + 1:]
            directory = os.path.dirname(relative_path)
            parser = configparser.SafeConfigParser()
            parser.read(index_file)

            for section in parser.sections():
                index_data = {
                    item[0]: item[1] for item in parser.items(section)
                }

                if not 'url' in index_data:
                    LOG.warning(str.format(
                        'skipped package section "{}" in {}: missing url',
                        section, index_file))
                    continue

                package = Package(git_url=index_data['url'], source=self.name,
                                  directory=directory, index_data=index_data)
                rval.append(package)

        return rval
