.. _bro-pkg:

bro-pkg Command-Line Tool
=========================

.. argparse::
    :module: bro-pkg
    :func: top_level_parser
    :prog: bro-pkg

    --configfile : @after
        See :ref:`bro-pkg-config-file`.

Commands
--------

.. _install-command:

install
~~~~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: install

.. _remove-command:

remove
~~~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: remove

.. _refresh-command:

refresh
~~~~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: refresh

.. _upgrade-command:

upgrade
~~~~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: upgrade

.. _load-command:

load
~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: load

.. _unload-command:

unload
~~~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: unload

.. _pin-command:

pin
~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: pin

.. _unpin-command:

unpin
~~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: unpin

.. _list-command:

list
~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: list

.. _search-command:

search
~~~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: search

.. _info-command:

info
~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: info

.. _config-command:

config
~~~~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: config

.. _env-command:

env
~~~
.. argparse::
    :module: bro-pkg
    :func: argparser
    :prog: bro-pkg
    :path: env

.. _bro-pkg-config-file:

Config File
-----------

The :program:`bro-pkg` command-line tool uses an INI-format config file to allow
users to customize their :doc:`Package Sources <source>`, :doc:`Package
<package>` installation paths, Bro executable/source paths, and other
:program:`bro-pkg` options.

See the default/example config file below for explanations of the
available options and how to customize them:

.. literalinclude:: ../bro-pkg.config
