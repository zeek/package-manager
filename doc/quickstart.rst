.. _PyPI: https://pypi.python.org/pypi

Quickstart Guide
================

Dependencies
------------

* Python 2.7+ or 3.0+
* git: https://git-scm.com
* GitPython: https://pypi.python.org/pypi/GitPython
* semantic_version: https://pypi.python.org/pypi/semantic_version

Note that following the suggested `Installation`_ process via :program:`pip`
will automatically install `GitPython` and `semantic_version` for you.

Installation
------------

Using the latest stable release on PyPI_:

.. code-block:: console

  $ pip install bro-pkg

Using the latest git development version:

.. code-block:: console

  $ pip install git+git://github.com/bro/package-manager@master

Basic Configuration
-------------------

After installing via :program:`pip`, additional configuration is required.
First, make sure that the :program:`bro-config` script that gets installed with
:program:`bro` is in your :envvar:`PATH`.  Then run:

.. code-block:: console

  $ mkdir -p ~/.bro-pkg
  $ bro-pkg autoconfig > ~/.bro-pkg/config

This automatically generates a config file with the following suggested
settings that should work for most Bro deployments:

- `script_dir`: set to the location of Bro's :file:`site` scripts directory
  (e.g. :file:`{<bro_install_prefix>}/share/bro/site`)

- `plugin_dir`: set to the location of Bro's default plugin directory (e.g.
  :file:`{<bro_install_prefix>}/lib/bro/plugins`)

- `bro_dist`: set to the location of Bro's source code.
  If you didn't build/install Bro from source code, this field will not be set,
  but it's only needed if you plan on installing packages that have uncompiled
  Bro plugins.

With those settings, the package manager will install Bro scripts, Bro plugins,
and BroControl plugins into directories where :program:`bro` and
:program:`broctl` will, by default, look for them.  BroControl clusters will
also automatically distribute installed package scripts/plugins to all nodes.

The final step is to edit your :file:`site/local.bro`.  If you want to
have Bro automatically load the scripts from all
:ref:`installed <install-command>` packages that are also marked as
":ref:`loaded <load-command>`" add:

.. code-block:: bro

  @load packages

If you prefer to manually pick the package scripts to load, you may instead add
lines like :samp:`@load {<package_name>}`, where :samp:`{<package_name>}`
is the :ref:`shorthand name <package-shorthand-name>` of the desired package.

If you want to further customize your configuration, see the `Advanced
Configuration`_ section and also  check :ref:`here <bro-pkg-config-file>` for a
full explanation of config file options.  Otherwise you're ready to use
:ref:`bro-pkg <bro-pkg>`.

Advanced Configuration
----------------------

If you prefer to not use the suggested `Basic Configuration`_ settings for
`script_dir` and `plugin_dir`, the default configuration will install all
package scripts/plugins within :file:`~/.bro-pkg` or you may change them to
whatever location you prefer.  These will be referred to as "non-standard"
locations in the sense that vanilla configurations of either :program:`bro` or
:program:`broctl` will not detect scripts/plugins in those locations without
additional configuration.

When using non-standard location, follow these steps to integrate with
:program:`bro` and :program:`broctl`:

- To get command-line :program:`bro` to be aware of Bro scripts/plugins in a
  non-standard location, make sure the :program:`bro-config` script (that gets
  installed along with :program:`bro`) is in your :envvar:`PATH` and run:

  .. code-block:: console

    $ `bro-pkg env`

  Note that this sets up the environment only for the current shell session.

- To get :program:`broctl` to be aware of scripts/plugins in a non-standard
  location, run:

  .. code-block:: console

    $ bro-pkg config script_dir

  And set the `SitePolicyPath` option in :file:`broctl.cfg` based on the output
  you see.  Similarly, run:

  .. code-block:: console

    $ bro-pkg config plugin_dir

  And set the `SitePluginPath` option in :file:`broctl.cfg` based on the output
  you see.

Usage
-----

Check the output of :ref:`bro-pkg --help <bro-pkg>` for an explanation of all
available functionality of the command-line tool.

.. note::
  The package manager currently lacks automatic dependency/version analysis,
  but in those cases the package author will likely document dependencies
  in their package's :file:`README` so that users can always install them
  manually.
