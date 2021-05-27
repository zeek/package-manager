.. _PyPI: https://pypi.python.org/pypi
.. _ZeekControl: https://github.com/zeek/zeekctl

Quickstart Guide
================

Dependencies
------------

* Python 3.6+
* git: https://git-scm.com
* GitPython: https://pypi.python.org/pypi/GitPython
* semantic_version: https://pypi.python.org/pypi/semantic_version
* btest: https://pypi.python.org/pypi/btest

Note that following the :program:`zkg` `Installation`_ process via
:program:`pip3` will automatically install its dependencies for you.

Installation
------------

* Zeek 4.0.0 or greater comes with a bundled :program:`zkg` that is
  included as part of its installation.  This is often the easiest choice since
  it comes pre-configured to work correctly for that particular Zeek
  installation and some `Basic Configuration`_ steps can be skipped.  The
  directions to build and install Zeek from source are here:
  https://docs.zeek.org/en/current/install/install.html

  Note that this method does require independent installation of
  :program:`zkg`'s dependencies, which is usually easiest to do via
  :program:`pip3`:

  .. code-block:: console

    $ pip3 install gitpython semantic-version

* To install the latest release of :program:`zkg` on PyPI_:

  .. code-block:: console

    $ pip3 install zkg

* To install the latest Git development version of :program:`zkg`:

  .. code-block:: console

    $ pip3 install git+git://github.com/zeek/package-manager@master

.. note::

  If not using something like :program:`virtualenv` to manage Python
  environments, the default user script directory is :file:`~/.local/bin` and
  you may have to modify your :envvar:`PATH` to search there for
  :program:`zkg`.

Basic Configuration
-------------------

:program:`zkg` supports four broad approaches to managing Zeek packages:

- Keep package metadata in :file:`$HOME/.zkg/` and maintain
  Zeek-relevant package content (such as scripts and plugins) in the
  Zeek installation tree. This is :program:`zkg`'s "traditional"
  approach.

- Keep all state and package content within the Zeek installation
  tree. Zeek 4's bundled :program:`zkg` installation provides this by
  default. If you use multiple Zeek installations in parallel, this
  approach allows you to install different sets of Zeek packages
  with each Zeek version.

- Keep all state and package content in :file:`$HOME/.zkg/`. This is
  the preferred approach when you're running :program:`zkg` and
  :program:`zeek` as different users. :program:`zkg`'s ``--user`` mode
  enables this approach.

- Custom configurations where you select your own state and content
  locations.

After installing via :program:`pip3`, but not when using the :program:`zkg`
that comes pre-bundled with a Zeek installation, additional configuration is
still required in the form of running a ``zkg autoconfig`` command, but in
either case, do read onward to get a better understanding of how the package
manager is configured, what directories it uses, etc.

To configure :program:`zkg` for use with a given Zeek installation, make
sure that the :program:`zeek-config` script that gets installed with
:program:`zeek` is in your :envvar:`PATH`.  Then, as the user you want to run
:program:`zkg` with, do:

.. code-block:: console

  $ zkg autoconfig

This automatically generates a config file with the following suggested
settings that should work for most Zeek deployments:

- `script_dir`: set to the location of Zeek's :file:`site` scripts directory
  (e.g. :file:`{<zeek_install_prefix>}/share/zeek/site`)

- `plugin_dir`: set to the location of Zeek's default plugin directory (e.g.
  :file:`{<zeek_install_prefix>}/lib/zeek/plugins`)

- `bin_dir`: set to the location where :program:`zkg` installs
  executables that packages provide (e.g.,
  :file:`{<zeek_install_prefix>}/bin`).

- `zeek_dist`: set to the location of Zeek's source code.
  If you didn't build/install Zeek from source code, this field will not be set,
  but it's only needed if you plan on installing packages that have uncompiled
  Zeek plugins.

With those settings, the package manager will install Zeek scripts, Zeek plugins,
and ZeekControl plugins into directories where :program:`zeek` and
:program:`zeekctl` will, by default, look for them.  ZeekControl clusters will
also automatically distribute installed package scripts/plugins to all nodes.

.. note::

  If your Zeek installation is owned by "root" and you intend to run
  :program:`zkg` as a different user, you have two options.

  First, you can use :program:`zkg`'s user mode (``zkg --user``). In
  user mode, :program:`zkg` consults :file:`$HOME/.zkg/config` for
  configuration settings. Creating this config file in user mode
  (``zkg --user autoconfig``) ensures that all state and content
  directories reside within :file:`$HOME/.zkg/`. :program:`zkg` reports
  according environment variables in the output of ``zkg --user env``.

  Second, you can grant "write" access to the directories specified by
  `script_dir`, `plugin_dir`, and `bin_dir`; perhaps using something like:

  .. code-block:: console

    $ sudo chgrp $USER $(zeek-config --site_dir) $(zeek-config
    --plugin_dir) $(zeek-config --prefix)/bin
    $ sudo chmod g+rwX $(zeek-config --site_dir) $(zeek-config --plugin_dir) $(zeek-config --prefix)/bin

The final step is to edit your :file:`site/local.zeek`.  If you want to
have Zeek automatically load the scripts from all
:ref:`installed <install-command>` packages that are also marked as
":ref:`loaded <load-command>`" add:

.. code-block:: bro

  @load packages

If you prefer to manually pick the package scripts to load, you may instead add
lines like :samp:`@load {<package_name>}`, where :samp:`{<package_name>}`
is the :ref:`shorthand name <package-shorthand-name>` of the desired package.

If you want to further customize your configuration, see the `Advanced
Configuration`_ section and also  check :ref:`here <zkg-config-file>` for a
full explanation of config file options.  Otherwise you're ready to use
:ref:`zkg <zkg>`.

Advanced Configuration
----------------------

If you prefer to not use the suggested `Basic Configuration`_ settings for
`script_dir` and `plugin_dir`, the default configuration will install all
package scripts/plugins within :file:`~/.zkg` or you may change them to
whatever location you prefer.  These will be referred to as "non-standard"
locations in the sense that vanilla configurations of either :program:`zeek` or
:program:`zeekctl` will not detect scripts/plugins in those locations without
additional configuration.

When using non-standard location, follow these steps to integrate with
:program:`zeek` and :program:`zeekctl`:

- To get command-line :program:`zeek` to be aware of Zeek scripts/plugins in a
  non-standard location, make sure the :program:`zeek-config` script (that gets
  installed along with :program:`zeek`) is in your :envvar:`PATH` and run:

  .. code-block:: console

    $ `zkg env`

  Note that this sets up the environment only for the current shell session.

- To get :program:`zeekctl` to be aware of scripts/plugins in a non-standard
  location, run:

  .. code-block:: console

    $ zkg config script_dir

  And set the `SitePolicyPath` option in :file:`zeekctl.cfg` based on the output
  you see.  Similarly, run:

  .. code-block:: console

    $ zkg config plugin_dir

  And set the `SitePluginPath` option in :file:`zeekctl.cfg` based on the output
  you see.

- To have your shell find executables that packages provide, update
  your :envvar:`PATH`:

  .. code-block:: console

    $ export PATH=$(zkg config bin_dir):$PATH

  (Executing ```zkg env```, as described above, includes this
  already.)

Usage
-----

Check the output of :ref:`zkg --help <zkg>` for an explanation of all
available functionality of the command-line tool.

Package Upgrades/Versioning
~~~~~~~~~~~~~~~~~~~~~~~~~~~

When installing packages, note that the :ref:`install command
<install-command>`, has a ``--version`` flag that may be used to install
specific package versions which may either be git release tags or branch
names.  The way that :program:`zkg` receives updates for a package
depends on whether the package is first installed to track stable
releases or a specific git branch.  See the :ref:`package upgrade
process <package-upgrade-process>` documentation to learn how
:program:`zkg` treats each situation.

Offline Usage
~~~~~~~~~~~~~

It's common to have limited network/internet access on the systems where
Zeek is deployed.  To accomodate those scenarios, :program:`zkg` can
be used as normally on a system that *does* have network access to
create bundles of its package installation environment. Those bundles
can then be transferred to the deployment systems via whatever means are
appropriate (SSH, USB flash drive, etc).

For example, on the package management system you can do typical package
management tasks, like install and update packages:

.. code-block:: console

    $ zkg install <package name>

Then, via the :ref:`bundle command <bundle-command>`, create a bundle
file which contains a snapshot of all currently installed packages:

.. code-block:: console

    $ zkg bundle zeek-packages.bundle

Then transfer :file:`zeek-packages.bundle` to the Zeek deployment
management host.  For Zeek clusters using ZeekControl_, this will
be the system acting as the "manager" node.  Then on that system
(assuming it already as :program:`zkg` installed and configured):

.. code-block:: console

    $ zkg unbundle zeek-packages.bundle

Finally, if you're using ZeekControl_, and the unbundling process
was successful, you need to deploy the changes to worker nodes:

.. code-block:: console

    $ zeekctl deploy
