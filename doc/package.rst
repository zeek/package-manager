Anatomy of a Package
====================

What is a Package?
------------------

The minimum requirement for a package is that it be a git repository containing
a metadata file named :file:`bro-pkg.meta` at its top-level that begins with the
line::

  [package]

This is the package's metadata file in INI file format and may contain
additional fields that describe the package as well as how it inter-operates
with Bro, the package manager, or other packages.

A package's shorthand name is simply the last component of of its git URL.  E.g.
a package at ``https://github.com/bro/foo`` may be referred to as **foo** and a
Bro script that wants to load all the scripts within that package can use:

.. code-block:: bro

  @load foo

Metadata
--------

version
~~~~~~~

The `version` field describes the current version of the package.  Use
the `Semantic Versioning <http://semver.org>`_ numbering scheme here.  An
example :file:`bro-pkg.meta`::

  [package]
  version = 1.0.0

Note that the version of a package can be different than the version of any Bro
or BroControl plugins that are contained in the package.

script_dir
~~~~~~~~~~

The `script_dir` field is a path relative to the root of the package that
contains a file named :file:`__load__.bro` and possibly other Bro scripts.

You may place any valid Bro script code within :file:`__load__.bro`, but a
package that contains many Bro scripts will typically have :file:`__load__.bro`
just contain a list of ``@load`` directives to load other Bro scripts within the
package.  E.g. if you have a package named **foo** installed, then it's
:file:`__load__.bro` will be what Bro loads when doing ``@load foo`` or running
``bro foo`` on the command-line.

An example :file:`bro-pkg.meta`::

  [package]
  version = 1.0.0
  script_dir = scripts

For a :file:`bro-pkg.meta` that looks like the above, the package should have a
file called :file:`scripts/__load__.bro`.

If the `script_dir` field is not present in :file:`bro-pkg.meta`, it defaults to
the top-level directory of the package, so a :file:`__load__.bro` script should
be located there.

plugin_dir
~~~~~~~~~~

The `plugin_dir` field is a path relative to the root of the package that
contains either pre-built `Bro Plugins`_, `BroControl Plugins`_, or both.

An example :file:`bro-pkg.meta`::

  [package]
  version = 1.0.0
  script_dir = scripts
  plugin_dir = plugins

For the above example, Bro and BroControl will load any plugins found in the
installed package's :file:`plugins/` directory.

If the `plugin_dir` field is not present in :file:`bro-pkg.meta`, it defaults to
a directory named :file:`build/` at the top-level of the package.  This is the
default location where Bro binary plugins get placed when building them from
source code (see `build_command`_).

build_command
~~~~~~~~~~~~~

The `build_command` field is an arbitrary shell command that the package manager
will run before installing the package.

This is useful for distributing `Bro Plugins`_ as source code and having the
package manager take care of building it on the user's machine before installing
the package.

An example :file:`bro-pkg.meta`::

  [package]
  version = 1.0.0
  script_dir = scripts/Demo/Rot13
  build_command = ./configure --bro-dist=%(bro_dist)s && make

In the above example, the ``%(bro_dist)s`` string is substituted for the path 
the user has set for the `bro_dist` option in the :ref:`package manager config
file <bro-pkg-config-file>`.

The default CMake skeleton for Bro plugins will use :file:`build/` as the
directory for the final/built version of the plugin, which matches the defaulted
value of the omitted `plugin_dir` metadata field.

The `script_dir` field is set to the location where the author has placed custom
scripts for their plugin.  When a package has both a Bro plugin and Bro script
components, the "plugin" part is always unconditionally loaded by Bro, but the
"script" components must either be explicitly loaded (e.g.
:samp:`@load {<package_name>}`) or the package marked as
:ref:`loaded <load-command>`.

Note that if you want to distribute a BroControl plugin along with a Bro plugin,
you may need to add the BroControl plugin's python script to the
``bro_plugin_dist_files()`` macro in the :file:`CMakeLists.txt` of the Bro
plugin so that it gets copied into :file:`build/` along with the built Bro
plugin.  Or you could also modify your `build_command` to copy it there.

bro
~~~

.. @todo: bro version dependency

dependencies
~~~~~~~~~~~~

.. @todo: inter-package dependencies

.. _Bro Plugins: https://www.bro.org/sphinx/devel/plugins.html
.. _BroControl Plugins:  https://www.bro.org/sphinx/components/broctl/README.html#plugins
