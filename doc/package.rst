Anatomy of a Package
====================

What is a Package?
------------------

The minimum requirement for a package is that it be a git repository containing
a metadata file named `pkg.meta` at its top-level that begins with the line::

  [package]

This is the package's metadata file in INI file format and may contain
additional fields that describe the package as well as how it inter-operates
with Bro, the package manager, or other packages.

A package's shorthand name is simply the last component of of its git URL.  E.g.
a package at `https://github.com/bro/foo` may be referred to as `foo` and a Bro
script that wants to load all the scripts within that package can use
"@load foo".

`version` metadata
------------------

The `version` field describes the current version of the package.  Use
the `Semantic Versioning <http://semver.org>`_ numbering scheme here.  An
example `pkg.meta`::

  [package]
  version = 1.0.0

`scriptpath` metadata
---------------------

The `scriptpath` field is a path relative to the root of the package that
contains a file named `__load__.bro` and possibly other Bro scripts.

You may place any valid Bro script code within `__load__.bro`, but a package
that contains many Bro scripts will typically have `__load__.bro` just contain a
list of `@load` directives to load other Bro scripts within the package.  E.g.
if you have a package named `foo` installed then, it's `__load__.bro` will be
what Bro loads when doing "@load foo" or running "bro foo" on the command-line.

An example `pkg.meta`::

  [package]
  version = 1.0.0
  scriptpath = scripts

For a `pkg.meta` that looks like the above, the package should have a file
called `scripts/__load__.bro`.

If the `scriptpath` field is not present in `pkg.meta`, it defaults to the top-
level directory of the package, so a `__load__.bro` script should be located
there.

`pluginpath` metadata
---------------------

The `pluginpath` field is a path relative to the root of the package that
contains either pre-built `Bro Plugins`_, `BroControl Plugins`_, or both.

An example `pkg.meta`::

  [package]
  version = 1.0.0
  scriptpath = scripts
  pluginpath = plugins

For the above example, Bro and BroControl will load any plugins found in the
installed package's `plugins/` directory.

If the `pluginpath` field is not present in `pkg.meta`, it defaults to a
directory named `build/` at the top-level of the package.  This is the default
location where Bro binary plugins get placed when building them from source
code (see `buildcmd metadata`_).

`buildcmd` metadata
-------------------

The `buildcmd` field is an arbitrary shell command that the package manager
will run before installing the package.

This is useful for distributing `Bro Plugins`_ as source code and having the
package manager take care of building it on the user's machine before installing
the package.

An example `pkg.meta`::

  [package]
  version = 1.0.0
  scriptpath = build/scripts
  buildcmd = ./configure --bro-dist=%(bro_dist)s && make

In the above example, the "%(bro_dist)s" string is substituted for the path the
user has set for the "bro_dist" option in the :ref:`package manager config file
<bro-pkg-config-file>`. The default CMake skeleton for Bro plugins will use
`build/` as the directory for the final/built version of the plugin, which
matches the defaulted value of the omitted `pluginpath` metadata field.

Note that if you want to distribute a BroControl plugin with a Bro plugin, you
typically need to add the BroControl plugin's python script to the
`bro_plugin_dist_files` macro in the `CMakeLists.txt` of the Bro plugin so
that it gets copied into `build/` along with the built Bro plugin.

`bro` metadata
--------------

@todo: bro version dependency

`dependencies` metadata
-----------------------

@todo: inter-package dependencies

`tags` metadata
---------------

@todo: discoverability metadata

.. _Bro Plugins: https://www.bro.org/sphinx/devel/plugins.html
.. _BroControl Plugins:  https://www.bro.org/sphinx/components/broctl/README.html#plugins
