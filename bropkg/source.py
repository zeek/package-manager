"""
A module containing the definition of a "package source": a git repository
containing a collection of git submodules that point to Bro packages.
"""

import os
import shutil
import git

from . import LOG
from .package import Package


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

    def packages(self):
        """Return list of :class:`.package.Package` in source repository."""
        rval = []

        for submodule in self.clone.submodules:
            module_dir = os.path.dirname(submodule.name)
            rval.append(Package(submodule.url, source=self.name,
                                module_dir=module_dir))

        return rval
