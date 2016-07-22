Command-Line Client (`bro-pkg`)
===============================

.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg

    install : @before
        .. _install-command:

    remove : @before
        .. _remove-command:

    refresh : @before
        .. _refresh-command:

    upgrade : @before
        .. _upgrade-command:

    load : @before
        .. _load-command:

    unload : @before
        .. _unload-command:

    pin : @before
        .. _pin-command:

    unpin : @before
        .. _unpin-command:

    list : @before
        .. _list-command:

    search : @before
        .. _search-command:

    info : @before
        .. _info-command:

    config : @before
        .. _config-command:

    env : @before
        .. _env-command:


.. _bro-pkg-config-file:

Config File
-----------

The `bro-pkg` command-line client uses an INI-format config file to allow users
to customize their :doc:`Package Sources <source>`, :doc:`Package <package>`
installation paths, Bro executable/source paths, and other package manager
client options.

See the default/example config file below for explanations of the
available options and how to customize them:

.. literalinclude:: ../bro-pkg.config
