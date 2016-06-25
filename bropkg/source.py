import os
import shutil
import git

from . import LOG
from .package import Package


class Source(object):

    def __init__(self, name, clone_path, git_url):
        """Create a package source.

        :raise git.exc.GitCommandError: if the git repo is invalid
        :raise OSError: if the git repo is invalid and can't be re-initialized
        """
        git_url = os.path.expanduser(git_url)
        self.name = name
        self.git_url = git_url

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
            old_urls = [url for url in self.clone.remote().urls]

            if git_url not in old_urls:
                LOG.debug(
                    'url of source "%s" changed from %s to %s, reclone at %s',
                    name, old_urls, git_url, clone_path)
                shutil.rmtree(clone_path)
                self.clone = git.Repo.clone_from(git_url, clone_path)

    def __str__(self):
        return self.git_url

    def __repr__(self):
        return self.git_url

    def packages(self):
        """Return list of `Package`s contained in source repository."""
        rval = []

        for submodule in self.clone.submodules:
            module_dir = os.path.dirname(submodule.name)
            rval.append(Package(submodule.url, source=self.name,
                                module_dir=module_dir))

        return rval
