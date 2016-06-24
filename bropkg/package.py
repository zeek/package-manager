class Package(object):

    def __init__(self, git_url, source=None, author=None, name=None):
        self.git_url = git_url
        self.name = name if name else git_url.split('/')[-1]
        self.source = source
        self.author = author

    def __str__(self):
        if self.source:
            if self.author:
                return '{}/{}/{}'.format(self.source, self.author, self.name)
            else:
                return '{}/{}'.format(self.source, self.name)
        else:
            return self.git_url

    def __repr__(self):
        return self.git_url

    def __lt__(self, other):
        return str(self) < str(other)

    def matches_path(self, path):
        """Return whether this package has a matching path/name."""
        if self.source:
            parts = path.split('/')

            if len(parts) == 1:
                return path == self.name

            if len(parts) == 2:
                if self.author:
                    return path == '{}/{}'.format(self.author, self.name)
                else:
                    return path == '{}/{}'.format(self.source, self.name)

            return path == '{}/{}/{}'.format(self.source, self.author,
                                             self.name)
        else:
            return path == self.git_url
