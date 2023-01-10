.. _bro-pkg:

.. _zkg:

zkg Command-Line Tool
=====================

.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :nosubcommands:

    --configfile : @after
        See :ref:`zkg-config-file`.

Commands
--------

.. _test-command:

test
~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: test

.. _install-command:

install
~~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: install

.. _remove-command:

remove
~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: remove

.. note::

   You may also say ``uninstall``.

.. _purge-command:

purge
~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: purge

.. _bundle-command:

bundle
~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: bundle

.. _unbundle-command:

unbundle
~~~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: unbundle

.. _refresh-command:

refresh
~~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: refresh

.. _upgrade-command:

upgrade
~~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: upgrade

.. _load-command:

load
~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: load

.. _unload-command:

unload
~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: unload

.. _pin-command:

pin
~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: pin

.. _unpin-command:

unpin
~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: unpin

.. _list-command:

list
~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: list

.. _search-command:

search
~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: search

.. _info-command:

info
~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: info

.. _config-command:

config
~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: config

.. _autoconfig-command:

autoconfig
~~~~~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: autoconfig

.. _env-command:

env
~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: env

create
~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: create

template info
~~~~~~~~~~~~~
.. argparse::
    :module: zkg
    :func: argparser
    :prog: zkg
    :path: template info

.. _bro-pkg-config-file:

.. _zkg-config-file:

Config File
-----------

The :program:`zkg` command-line tool uses an INI-format config file to allow
users to customize their :doc:`Package Sources <source>`, :doc:`Package
<package>` installation paths, Zeek executable/source paths, and other
:program:`zkg` options.

See the default/example config file below for explanations of the
available options and how to customize them:

.. literalinclude:: ../zkg.config
