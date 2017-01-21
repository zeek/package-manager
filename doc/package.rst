.. _Bro Scripting: https://www.bro.org/sphinx/scripting/index.html
.. _Bro Plugins: https://www.bro.org/sphinx/devel/plugins.html
.. _BroControl Plugins:  https://www.bro.org/sphinx/components/broctl/README.html#plugins
.. _Semantic Version Specification: https://python-semanticversion.readthedocs.io/en/latest/reference.html#version-specifications-the-spec-class
.. _btest: https://github.com/bro/btest

How-To: Create a Package
========================

A Bro package may contain Bro scripts, Bro plugins, or BroControl plugins.  Any
number or combination of those components may be included within a single
package.

The minimum requirement for a package is that it be a git repository containing
a metadata file named :file:`bro-pkg.meta` at its top-level that begins with the
line::

  [package]

This is the package's metadata file in INI file format and may contain
:ref:`additional fields <metadata-fields>` that describe the package as well
as how it inter-operates with Bro, the package manager, or other packages.

.. _package-shorthand-name:

Note that the shorthand name for your package that may be used by :ref:`bro-pkg
<bro-pkg>` and Bro script :samp:`@load {<package_name>}` directives will be the
last component of its git URL. E.g. a package at ``https://github.com/bro/foo``
may be referred to as **foo** when using :program:`bro-pkg` and a Bro
script that wants to load all the scripts within that package can use:

.. code-block:: bro

  @load foo

Walkthroughs
------------

Pure Bro Script Package
~~~~~~~~~~~~~~~~~~~~~~~

#. Create a git repository:

   .. code-block:: console

      $ mkdir foo && cd foo && git init

#. Create a package metadata file, :file:`bro-pkg.meta`:

   .. code-block:: console

      $ echo '[package]' > bro-pkg.meta

#. Create a :file:`__load__.bro` script with example code in it:

   .. code-block:: console

      $ echo 'event bro_init() { print "foo is loaded"; }' > __load__.bro

#. (Optional) Relocate your :file:`__load__.bro` script to any subdirectory:

   .. code-block:: console

      $ mkdir scripts && mv __load__.bro scripts
      $ echo 'script_dir = scripts' >> bro-pkg.meta

#. Commit everything to git:

   .. code-block:: console

      $ git add * && git commit -m 'First commit'

#. (Optional) Test that Bro correctly loads the script after installing the
   package with :program:`bro-pkg`:

   .. code-block:: console

      $ bro-pkg install .
      $ bro foo
      $ bro-pkg remove .

#. (Optional) :ref:`Create a release version tag <package-versioning>`.

See `Bro Scripting`_ for more information on developing Bro scripts.

Binary Bro Plugin Package
~~~~~~~~~~~~~~~~~~~~~~~~~

#. Create a plugin skeleton using :file:`aux/bro-aux/plugin-support/init-plugin`
   from Bro's source distribution:

   .. code-block:: console

      $ init-plugin ./rot13 Demo Rot13

#. Create a git repository

   .. code-block:: console

      $ cd rot13 && git init

#. Create a package metadata file, :file:`bro-pkg.meta`::

     [package]
     script_dir = scripts/Demo/Rot13
     build_command = ./configure --bro-dist=%(bro_dist)s && make

#. Add example script code:

   .. code-block:: console

      $ echo 'event bro_init() { print "rot13 plugin is loaded"; }' >> scripts/__load__.bro
      $ echo 'event bro_init() { print "rot13 script is loaded"; }' >> scripts/Demo/Rot13/__load__.bro

#. Add an example builtin-function in :file:`src/rot13.bif`:

   .. code-block:: c++

      module Demo;

      function rot13%(s: string%) : string
          %{
          char* rot13 = copy_string(s->CheckString());

          for ( char* p = rot13; *p; p++ )
              {
              char b = islower(*p) ? 'a' : 'A';
              *p  = (*p - b + 13) % 26 + b;
              }

          BroString* bs = new BroString(1, reinterpret_cast<byte_vec>(rot13),
                                        strlen(rot13));
          return new StringVal(bs);
          %}

#. Commit everything to git:

   .. code-block:: console

      $ git add * && git commit -m 'First commit'

#. (Optional) Test that Bro correctly loads the plugin after installing the
   package with :program:`bro-pkg`:

   .. code-block:: console

      $ bro-pkg install .
      $ bro rot13 -e 'print Demo::rot13("Hello")'
      $ bro-pkg remove .

#. (Optional) :ref:`Create a release version tag <package-versioning>`.

See `Bro Plugins`_ for more information on developing Bro plugins.

BroControl Plugin Package
~~~~~~~~~~~~~~~~~~~~~~~~~

#. Create a git repository:

   .. code-block:: console

      $ mkdir foo && cd foo && git init

#. Create a package metadata file, :file:`bro-pkg.meta`:

   .. code-block:: console

      $ echo '[package]' > bro-pkg.meta

#. Create an example BroControl plugin, :file:`foo.py`:

   .. code-block:: python

      import BroControl.plugin
      from BroControl import config

      class Foo(BroControl.plugin.Plugin):
          def __init__(self):
              super(Foo, self).__init__(apiversion=1)

          def name(self):
              return "foo"

          def pluginVersion(self):
              return 1

          def init(self):
              self.message("foo plugin is initialized")
              return True

#. Set the `plugin_dir` metadata field to directory where the plugin is located:

   .. code-block:: console

      $ echo 'plugin_dir = .' >> bro-pkg.meta

#. Commit everything to git:

   .. code-block:: console

      $ git add * && git commit -m 'First commit'

#. (Optional) Test that BroControl correctly loads the plugin after installing
   the package with :program:`bro-pkg`:

   .. code-block:: console

      $ bro-pkg install .
      $ broctl
      $ bro-pkg remove .

#. (Optional) :ref:`Create a release version tag <package-versioning>`.

See `BroControl Plugins`_ for more information on developing BroControl plugins.

If you want to distribute a BroControl plugin along with a Bro plugin in the
same package, you may need to add the BroControl plugin's python script to the
``bro_plugin_dist_files()`` macro in the :file:`CMakeLists.txt` of the Bro
plugin so that it gets copied into :file:`build/` along with the built Bro
plugin.  Or you could also modify your `build_command` to copy it there, but
what ultimately matters is that the `plugin_dir` field points to a directory
that contains both the Bro plugin and the BroControl plugin.

Registering to a Package Source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Registering a package to a package source is always the following basic steps:

#) Create a :ref:`Package Index File <package-index-file>` for your package.
#) Add the index file to the package source's git repository.

The full process and conventions for submitting to the default package source
can be found in the :file:`README` at:

  https://github.com/bro/packages

.. _metadata-fields:

Package Metadata
----------------

See the following sub-sections for a full list of available fields that may be
used in :file:`bro-pkg.meta` files.

description
~~~~~~~~~~~

The description field may be used to give users a general overview of the
package and its purpose. The :ref:`bro-pkg list <list-command>` will display
the first sentence of description fields in the listings it displays.  An
example :file:`bro-pkg.meta` using a description field::

  [package]
  description = Another example package.
      The description text may span multiple
      line: when adding line breaks, just
      indent the new lines so they are parsed
      as part of the 'description' value.

tags
~~~~

The `tags` field contains a comma-delimited set of metadata tags that further
classify and describe the purpose of the package.  This is used to help users
better discover and search for packages.  The
:ref:`bro-pkg search <search-command>` command will inspect these tags.  An
example :file:`bro-pkg.meta` using tags::

  [package]
  tags = bro plugin, broctl plugin, scan detection, intel

Suggested Tags
^^^^^^^^^^^^^^

Some ideas for what to put in the `tags` field for packages:

- bro scripting

  - conn
  - intel
  - geolocation
  - file analysis
  - sumstats, summary statistics
  - input
  - log, logging
  - notices

- *<network protocol name>*

- *<file format name>*

- signatures

- bro plugin

  - protocol analyzer
  - file analyzer
  - bifs
  - packet source
  - packet dumper
  - input reader
  - log writer

- broctl plugin

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
  script_dir = scripts

For a :file:`bro-pkg.meta` that looks like the above, the package should have a
file called :file:`scripts/__load__.bro`.

If the `script_dir` field is not present in :file:`bro-pkg.meta`, it
defaults to checking the top-level directory of the package for a
:file:`__load__.bro` script.  If it's found there, :program:`bro-pkg`
use the top-level package directory as the value for `script_dir`.  If
it's not found, then :program:`bro-pkg` assumes the package contains no
Bro scripts (which may be the case for some plugins).

plugin_dir
~~~~~~~~~~

The `plugin_dir` field is a path relative to the root of the package that
contains either pre-built `Bro Plugins`_, `BroControl Plugins`_, or both.

An example :file:`bro-pkg.meta`::

  [package]
  script_dir = scripts
  plugin_dir = plugins

For the above example, Bro and BroControl will load any plugins found in the
installed package's :file:`plugins/` directory.

If the `plugin_dir` field is not present in :file:`bro-pkg.meta`, it defaults
to a directory named :file:`build/` at the top-level of the package.  This is
the default location where Bro binary plugins get placed when building them from
source code (see `build_command`_).

build_command
~~~~~~~~~~~~~

The `build_command` field is an arbitrary shell command that the package
manager will run before installing the package.

This is useful for distributing `Bro Plugins`_ as source code and having the
package manager take care of building it on the user's machine before installing
the package.

An example :file:`bro-pkg.meta`::

  [package]
  script_dir = scripts/Demo/Rot13
  build_command = ./configure --bro-dist=%(bro_dist)s && make

In the above example, the ``%(bro_dist)s`` string is substituted for the path 
the user has set for the `bro_dist` field in the :ref:`package manager config
file <bro-pkg-config-file>`.

The default CMake skeleton for Bro plugins will use :file:`build/` as the
directory for the final/built version of the plugin, which matches the defaulted
value of the omitted `plugin_dir` metadata field.

The `script_dir` field is set to the location where the author has placed
custom scripts for their plugin.  When a package has both a Bro plugin and Bro
script components, the "plugin" part is always unconditionally loaded by Bro,
but the "script" components must either be explicitly loaded (e.g. :samp:`@load
{<package_name>}`) or the package marked as :ref:`loaded <load-command>`.

test_command
~~~~~~~~~~~~

The `test_command` field is an arbitrary shell command that the package manager
will run when a user either manually runs the :ref:`test command <test-command>`
or before the package is installed or upgraded.

An example :file:`bro-pkg.meta`::

  [package]
  test_command = cd testing && btest -d tests

The recommended test framework for writing package unit tests is `btest`_.
See its documentation for further explanation and examples.

config_files
~~~~~~~~~~~~

The `config_files` field may be used to specify a list of files that users
are intended to directly modify after installation.  Then, on operations that
would otherwise destroy a user's local modifications to a config file, such
as upgrading to a newer package version, :program:`bro-pkg` can instead save
a backup and possibly prompt the user to review the differences.

An example :file:`bro-pkg.meta`::

  [package]
  script_dir = scripts
  config_files = scripts/foo_config.bro, scripts/bar_config.bro

The value of `config_files` is a comma-delimited string of config file paths
that are relative to the root directory of the package.  Config files should
either be located within the `script_dir` or `plugin_dir`.

.. _package-dependencies:

depends
~~~~~~~

The `depends` field may be used to specify a list of dependencies that the
package requires.

An example :file:`bro-pkg.meta`::

  [package]
  depends =
    bro >=2.5.0
    foo *
    https://github.com/bro/bar >=2.0.0
    package_source/path/bar branch=name_of_git_branch

The field is a list of dependency names and their version requirement
specifications.

A dependency name may be either `bro`, a full git URL of the package, or a
:ref:`package shorthand name <package-shorthand-name>`.

- The special `bro` dependency refers not to a package, but the version of
  Bro that the package requires in order to function.  If the user has
  :program:`bro-config` in their :envvar:`PATH` when installing/upgrading a
  package that specifies a `bro` dependency, then :program:`bro-pkg` will
  enforce that the requirement is satisfied.

- The full git URL may be directly specified in the `depends` metadata if you
  want to force the dependency to always resolve to a single, canonical git
  repository.  Typically this is the safe approach to take when listing
  package dependencies and for publicly visible packages.

- When using shorthand package dependency names, the user's :program:`bro-pkg`
  will try to resolve the name into a full git URL based on the package sources
  they have configured.  Typically this approach may be most useful for internal
  or testing environments.

A version requirement may be either a git branch name or a semantic version
specification. When using a branch as a version requirement, prefix the
branchname with ``branch=``, else see the `Semantic Version Specification`_
documentation for the complete rule set of acceptable version requirement
strings.  Here's a summary:

  - ``*``: any version (this will also satisfy/match on git branches)
  - ``<1.0.0``: versions less than 1.0.0
  - ``<=1.0.0``: versions less than or equal to 1.0.0
  - ``>1.0.0``: versions greater than 1.0.0
  - ``>=1.0.0``: versions greater than or equal to 1.0.0
  - ``==1.0.0``: exactly version 1.0.0
  - ``!=1.0.0``: versions not equal to 1.0.0
  - ``^1.3.4``: versions between 1.3.4 and 2.0.0 (not including 2.0.0)
  - ``~1.2.3``: versions between 1.2.3 and  1.3.0 (not including 1.3.0)
  - ``~=2.2``: versions between 2.2.0 and 3.0.0 (not included 3.0.0)
  - ``~=1.4.5``: versions between 1.4.5 and 1.5.0 (not including 3.0.0)
  - Any of the above may be combined by a separating comma to logically "and"
    the requirements together.  E.g. ``>=1.0.0,<2.0.0`` means "greater or equal
    to 1.0.0 and less than 2.0.0".

.. _package-versioning:

Package Versioning
------------------

Creating New Package Release Versions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Package's should use git tags for versioning their releases.
Use the `Semantic Versioning <http://semver.org>`_ numbering scheme
here.  For example, to create a new tag for a package:

   .. code-block:: console

      $ git tag -a 1.0.0 -m 'Release 1.0.0'

Then, assuming you've already set up a public/remote git repository
(e.g. on GitHub) for your package, remember to push the tag to the
remote repository:

   .. code-block:: console

      $ git push --tags

Alternatively, if you expect to have a simple development process for
your package, you may choose to not create any version tags and just
always make commits directly to your package's *master* branch.  Users
will receive package updates differently depending on whether you decide
to use release version tags or not.  See the :ref:`package upgrade
process <package-upgrade-process>` documentation for more details on
the differences.

.. _package-upgrade-process:

Package Upgrade Process
~~~~~~~~~~~~~~~~~~~~~~~

The :ref:`install command <install-command>` will either install a
stable release version or the latest commit on a specific git branch of
a package.

The default installation behavior of :program:`bro-pkg` is to look for
the latest release version tag and install that.  If there are no such
version tags, it will fall back to installing the latest commit of the
package's *master* branch

Upon installing a package via a git version tag, the
:ref:`upgrade command <upgrade-command>` will only upgrade the local
installation of that package if a greater version tag is available.  In
other words, you only receive stable release upgrades for packages
installed in this way.

Upon installing a package via a git branch name, the :ref:`upgrade
command <upgrade-command>` will upgrade the local installation of the
package whenever a new commit becomes available at the end of the
branch.  This method of tracking packages is suitable for testing out
development/experimental versions of packages.
