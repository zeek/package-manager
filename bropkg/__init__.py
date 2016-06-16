import logging

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())


def version_string():
    # @todo: autogenerate this
    return '0.1'


class Manager(object):

    def __init__(self, sources, cache_path):
        LOG.debug('init Manager version %s', version_string())
        self.sources = sources
        self.cache_path = cache_path

    def default_source(self):
        return self.sources['default']
