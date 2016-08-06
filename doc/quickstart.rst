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

Using the latest stable release on PyPI_::

  pip install bro-pkg

Using the latest git development version::

  pip install git+git://github.com/bro/package-manager@master

Basic Configuration
-------------------

After first installation via :program:`pip`, additional configuration is
required::

  mkdir -p ~/.bro-pkg
  bro-pkg config > ~/.bro-pkg/config

Now edit :file:`~/.bro-pkg/config` and fill in appropriate option values.
Check :ref:`here <bro-pkg-config-file>` for a full explanation of what each
does, or set the following suggested settings that are likely to work for most
scenarios:

- set `scriptdir` to the location of Bro's :file:`site` scripts directory (e.g.
  :file:`{<bro_install_prefix>}/share/bro/site`)

- set `plugindir` to the location of Bro's default plugin directory (e.g.
  :file:`{<bro_install_prefix>}/lib/bro/plugins`)

- set `bro_dist` to the location of Bro's source code (if you plan on
  installing packages that have Bro plugins that require compilation).

With those settings, the package manager will install Bro scripts, Bro plugins,
and BroControl plugins into directories where :program:`bro` and
:program:`broctl` will, by default, look for them.  BroControl clusters will
also automatically distribute installed package scripts/plugins to all nodes.

The final step is to edit your :file:`site/local.bro`.  If you want to
automatically load the scripts from all packages
:ref:`installed <install-command>` and :ref:`loaded <load-command>`
via :ref:`bro-pkg <bro-pkg>`, add::

  @load packages

Advanced Configuration
----------------------

If you prefer to not use the suggested `Basic Configuration`_ settings for
`scriptdir` and `plugindir`, the default configuration will install all package
scripts/plugins within :file:`~/.bro-pkg` or you may change them to whatever
location you prefer.  These will be referred to as "non-standard" locations in
the sense that vanilla configurations of either :program:`bro` or
:program:`broctl` will not detect scripts/plugins in those locations without
additional configuration.

When using non-standard location, follow these steps to integrate with
:program:`bro` and :program:`broctl`:

- To get command-line :program:`bro` to be aware of Bro scripts/plugins in a
  non-standard location, set the `bro_exe` config option and run the
  :ref:`bro-pkg env <env-command>` command to help you set up your environment.

- To get :program:`broctl` to be aware of scripts/plugins in a non-standard
  location, edit :file:`broctl.cfg` and adjust `SitePolicyPath`
  and `SitePluginPath` according to the output of
  ``bro-pkg config scriptdir`` and ``bro-pkg config plugindir``,
  respectively.

.. _PyPI: https://pypi.python.org/pypi
