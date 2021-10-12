.. _Zeek Scripting: https://docs.zeek.org/en/stable/examples/scripting/index.html
.. _Zeek Plugins: https://docs.zeek.org/en/stable/devel/plugins.html
.. _ZeekControl Plugins: https://github.com/zeek/zeekctl#plugins
.. _Semantic Version Specification: https://python-semanticversion.readthedocs.io/en/latest/reference.html#version-specifications-the-spec-class
.. _btest: https://github.com/zeek/btest
.. _configparser interpolation: https://docs.python.org/3/library/configparser.html#interpolation-of-values

How-To: Create a Package
========================

A Zeek package may contain Zeek scripts, Zeek plugins, or ZeekControl plugins.  Any
number or combination of those components may be included within a single
package.

The minimum requirement for a package is that it be in its own git repository
and contain a metadata file named :file:`zkg.meta` at its top-level that
begins with the line::

  [package]

This is the package's metadata file in INI file format and may contain
:ref:`additional fields <metadata-fields>` that describe the package as well
as how it inter-operates with Zeek, the package manager, or other packages.

.. note::

   :file:`zkg.meta` is the canonical metadata file name used :program:`since
   zkg v2.0`.  The previous metadata file name of :file:`bro-pkg.meta` is also
   accepted when no :file:`zkg.meta` exists.

.. _package-shorthand-name:

Note that the shorthand name for your package that may be used by :ref:`zkg
<zkg>` and Zeek script :samp:`@load {<package_name>}` directives will be the
last component of its git URL. E.g. a package at ``https://github.com/zeek/foo``
may be referred to as **foo** when using :program:`zkg` and a Zeek
script that wants to load all the scripts within that package can use:

.. code-block:: bro

  @load foo

Bootstrapping packages with :program:`zkg`
------------------------------------------

The easiest way to start a new Zeek package is via :program:`zkg`
itself: its ``zkg create`` command lets you generate new Zeek packages
from the command line.

This functionality is available since :program:`since zkg v2.9`.  See the
:ref:`Walkthroughs <manual-package-creation>` section for step-by-step
processes that show how to manually create packages (e.g. perhaps when using
older :program:`zkg` versions).

Concepts
~~~~~~~~

:program:`zkg` instantiates new packages from a *package template*.
Templates are standalone git repositories. The URL of :program:`zkg`'s
default template is https://github.com/zeek/package-template, but you
can provide your own.

.. note::

   At :program:`zkg` configuration time, the ``ZKG_DEFAULT_TEMPLATE``
   environment variable lets you override the default, and the
   ``--template`` argument to ``zkg create`` allows overrides upon
   instantiation. You can review the template :program:`zkg` will use
   by default via the ``zkg config`` command's output.

A template provides a basic *package* layout, with optional added
*features* that enhance the package. For example, the default template
lets you add a native-code plugin and support for GitHub actions.

Templates are parameterized via :ref:`user variables <user-vars>`.
These variables provide the basic configuration required when
instantiating the template, for example to give the package a name. A
template uses resolved user variables to populate internal
*parameters* that the template requires. Think of parameters as
derivatives of the user variables, for example to provide different
capitalizations or suffixes.

A template operates as a :program:`zkg` plugin, including runnable
Python code. This code has full control over how a package gets
instantiated, defining required user variables and features,
and possibly customizing content production.

The ``create`` command
~~~~~~~~~~~~~~~~~~~~~~

When using the ``zkg create`` command, you specify an output directory
for the new package tree, name the features you'd like to add, and
optionally define user variables. :program:`zkg` will prompt
you for any variables it still needs to resolve, and guides you
through the package creation. A basic invocation might look as follows:

.. code-block:: console

    $ zkg create --packagedir foobar --feature plugin
    "package-template" requires a "name" value (the name of the package, e.g. "FooBar"):
    name: Foobar
    "package-template" requires a "namespace" value (a namespace for the package, e.g. "MyOrg"):
    namespace: MyOrg

The resulting package now resides in the ``foobar`` directory.
Unless you provide ``--force``, :program:`zkg` will not overwrite an
existing package. When the requested output directory exists, it will
prompt for permission to delete the existing directory.

After instantiation, the package is immediately installable via
:program:`zkg`. You'll see details of how it got generated in its
initial commit, and the newly minted ``zkg.meta`` has details of the
provided user variables:

.. code-block:: console

    $ cat foobar/zkg.meta
    ...
    [template]
    source = package-template
    version = master
    zkg_version = 2.8.0
    features = plugin

    [template_vars]
    name = Foobar
    namespace = MyOrg

This information is currently informational only, but in the future
will enable baselining changes in package templates to assist with
package modernization.

To keep templates in sync with :program:`zkg` versions, templates
employ semantic API versioning. An incompatible template will refuse
to load and lead to an according error message. Much like Zeek
packages, templates support git-level versioning to accommodate
compatibility windows.

See the output of ``zkg create --help`` for a complete summary of the
available options.

Obtaining information about a template
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The best source for the capabilities of a template is its
documentation, but to get a quick overview of a given template's
features and user variables, consider the ``zkg template info``
command, which summarizes a template in plain text, or in JSON when
invoked with the ``--json`` argument.

.. _manual-package-creation:

Walkthroughs
------------

For historical reference, the following sections cover manual ways of
establishing Zeek packages.

Pure Zeek Script Package
~~~~~~~~~~~~~~~~~~~~~~~~

#. Create a git repository:

   .. code-block:: console

      $ mkdir foo && cd foo && git init

#. Create a package metadata file, :file:`zkg.meta`:

   .. code-block:: console

      $ echo '[package]' > zkg.meta

#. Create a :file:`__load__.zeek` script with example code in it:

   .. code-block:: console

      $ echo 'event zeek_init() { print "foo is loaded"; }' > __load__.zeek

#. (Optional) Relocate your :file:`__load__.zeek` script to any subdirectory:

   .. code-block:: console

      $ mkdir scripts && mv __load__.zeek scripts
      $ echo 'script_dir = scripts' >> zkg.meta

#. Commit everything to git:

   .. code-block:: console

      $ git add * && git commit -m 'First commit'

#. (Optional) Test that Zeek correctly loads the script after installing the
   package with :program:`zkg`:

   .. code-block:: console

      $ zkg install .
      $ zeek foo
      $ zkg remove .

#. (Optional) :ref:`Create a release version tag <package-versioning>`.

See `Zeek Scripting`_ for more information on developing Zeek scripts.

Binary Zeek Plugin Package
~~~~~~~~~~~~~~~~~~~~~~~~~~

See `Zeek Plugins`_ for more complete information on developing Zeek plugins,
though the following step are the essentials needed to create a package.


#. Create a plugin skeleton using :file:`aux*/zeek-aux/plugin-support/init-plugin`
   from Zeek's source distribution:

   .. code-block:: console

      $ init-plugin ./rot13 Demo Rot13

#. Create a git repository

   .. code-block:: console

      $ cd rot13 && git init

#. Create a package metadata file, :file:`zkg.meta`::

     [package]
     script_dir = scripts/Demo/Rot13
     build_command = ./configure && make

   .. note::

      See :ref:`legacy-bro-support` for notes on configuring packages to
      support Bro 2.5 or earlier.

#. Add example script code:

   .. code-block:: console

      $ echo 'event zeek_init() { print "rot13 plugin is loaded"; }' >> scripts/__load__.zeek
      $ echo 'event zeek_init() { print "rot13 script is loaded"; }' >> scripts/Demo/Rot13/__load__.zeek

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

#. (Optional) Test that Zeek correctly loads the plugin after installing the
   package with :program:`zkg`:

   .. code-block:: console

      $ zkg install .
      $ zeek rot13 -e 'print Demo::rot13("Hello")'
      $ zkg remove .

#. (Optional) :ref:`Create a release version tag <package-versioning>`.

ZeekControl Plugin Package
~~~~~~~~~~~~~~~~~~~~~~~~~~

#. Create a git repository:

   .. code-block:: console

      $ mkdir foo && cd foo && git init

#. Create a package metadata file, :file:`zkg.meta`:

   .. code-block:: console

      $ echo '[package]' > zkg.meta

#. Create an example ZeekControl plugin, :file:`foo.py`:

   .. code-block:: python

      import ZeekControl.plugin
      from ZeekControl import config

      class Foo(ZeekControl.plugin.Plugin):
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

      $ echo 'plugin_dir = .' >> zkg.meta

#. Commit everything to git:

   .. code-block:: console

      $ git add * && git commit -m 'First commit'

#. (Optional) Test that ZeekControl correctly loads the plugin after installing
   the package with :program:`zkg`:

   .. code-block:: console

      $ zkg install .
      $ zeekctl
      $ zkg remove .

#. (Optional) :ref:`Create a release version tag <package-versioning>`.

See `ZeekControl Plugins`_ for more information on developing ZeekControl plugins.

If you want to distribute a ZeekControl plugin along with a Zeek plugin in the
same package, you may need to add the ZeekControl plugin's python script to the
``zeek_plugin_dist_files()`` macro in the :file:`CMakeLists.txt` of the Zeek
plugin so that it gets copied into :file:`build/` along with the built Zeek
plugin.  Or you could also modify your `build_command` to copy it there, but
what ultimately matters is that the `plugin_dir` field points to a directory
that contains both the Zeek plugin and the ZeekControl plugin.

Registering to a Package Source
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Registering a package to a package source is always the following basic steps:

#) Create a :ref:`Package Index File <package-index-file>` for your package.
#) Add the index file to the package source's git repository.

The full process and conventions for submitting to the default package source
can be found in the :file:`README` at:

  https://github.com/zeek/packages

.. _metadata-fields:

Package Metadata
----------------

See the following sub-sections for a full list of available fields that may be
used in :file:`zkg.meta` files.

`description` field
~~~~~~~~~~~~~~~~~~~

The description field may be used to give users a general overview of the
package and its purpose. The :ref:`zkg list <list-command>` will display
the first sentence of description fields in the listings it displays.  An
example :file:`zkg.meta` using a description field::

  [package]
  description = Another example package.
      The description text may span multiple
      line: when adding line breaks, just
      indent the new lines so they are parsed
      as part of the 'description' value.

`aliases` field
~~~~~~~~~~~~~~~

The `aliases` field can be used to specify alternative names for a
package.  Users can then use :samp:`@load {<package_alias>}` for
any alias listed in this field.  This may be useful when renaming a
package's repository on GitHub while still supporting users that already
installed the package under the previous name.  For example, if package
`foo` were renamed to `foo2`, then the `aliases` for it could be::

  [package]
  aliases = foo2 foo

Currently, the order does not matter, but you should specify the
canonical/current alias first.  The list is delimited by commas or
whitespace.  If this field is not specified, the default behavior is the
same as if using a single alias equal to the package's name.

The low-level details of the way this field operates is that, for each alias,
it simply creates a symlink of the same name within the directory associated
with the ``script_dir`` path in the :ref:`config file <zkg-config-file>`.

Available :program:`since bro-pkg v1.5`.

`credits` field
~~~~~~~~~~~~~~~

The `credits` field contains a comma-delimited set of
author/contributor/maintainer names, descriptions, and/or email
addresses.

It may be used if you have particular requirements or concerns regarding
how authors or contributors for your package are credited in any public
listings made by external metadata scraping tools (:program:`zkg`
does not itself use this data directly for any functional purpose).  It
may also be useful as a standardized location for users to get
contact/support info in case they encounter problems with the package.
For example::

    [package]
    credits = A. Sacker <ace@sacker.com>.,
        JSON support added by W00ter (Acme Corporation)

`tags` field
~~~~~~~~~~~~

The `tags` field contains a comma-delimited set of metadata tags that further
classify and describe the purpose of the package.  This is used to help users
better discover and search for packages.  The
:ref:`zkg search <search-command>` command will inspect these tags.  An
example :file:`zkg.meta` using tags::

  [package]
  tags = zeek plugin, zeekctl plugin, scan detection, intel

Suggested Tags
^^^^^^^^^^^^^^

Some ideas for what to put in the `tags` field for packages:

- zeek scripting

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

- zeek plugin

  - protocol analyzer
  - file analyzer
  - bifs
  - packet source
  - packet dumper
  - input reader
  - log writer

- zeekctl plugin

`script_dir` field
~~~~~~~~~~~~~~~~~~

The `script_dir` field is a path relative to the root of the package that
contains a file named :file:`__load__.zeek` and possibly other Zeek scripts. The
files located in this directory are copied into
:file:`{<user_script_dir>}/packages/{<package>}/`, where `<user_script_dir>`
corresponds to the `script_dir` field of the user's
:ref:`config file <zkg-config-file>` (typically
:file:`{<zeek_install_prefix>}/share/zeek/site`).

When the package is :ref:`loaded <load-command>`,
an :samp:`@load {<package_name>}` directive is
added to :file:`{<user_script_dir>}/packages/packages.zeek`.

You may place any valid Zeek script code within :file:`__load__.zeek`, but a
package that contains many Zeek scripts will typically have :file:`__load__.zeek`
just contain a list of ``@load`` directives to load other Zeek scripts within the
package.  E.g. if you have a package named **foo** installed, then it's
:file:`__load__.zeek` will be what Zeek loads when doing ``@load foo`` or running
``zeek foo`` on the command-line.

An example :file:`zkg.meta`::

  [package]
  script_dir = scripts

For a :file:`zkg.meta` that looks like the above, the package should have a
file called :file:`scripts/__load__.zeek`.

If the `script_dir` field is not present in :file:`zkg.meta`, it
defaults to checking the top-level directory of the package for a
:file:`__load__.zeek` script.  If it's found there, :program:`zkg`
use the top-level package directory as the value for `script_dir`.  If
it's not found, then :program:`zkg` assumes the package contains no
Zeek scripts (which may be the case for some plugins).

`plugin_dir` field
~~~~~~~~~~~~~~~~~~

The `plugin_dir` field is a path relative to the root of the package that
contains either pre-built `Zeek Plugins`_, `ZeekControl Plugins`_, or both.

An example :file:`zkg.meta`::

  [package]
  script_dir = scripts
  plugin_dir = plugins

For the above example, Zeek and ZeekControl will load any plugins found in the
installed package's :file:`plugins/` directory.

If the `plugin_dir` field is not present in :file:`zkg.meta`, it defaults
to a directory named :file:`build/` at the top-level of the package.  This is
the default location where Zeek binary plugins get placed when building them from
source code (see the `build_command field`_).

This field may also be set to the location of a tarfile that has a single top-
level directory inside it containing the Zeek plugin. The default CMake skeleton
for Zeek plugins produces such a tarfile located at
:file:`build/<namespace>_<plugin>.tgz`. This is a good choice to use for
packages that will be published to a wider audience as installing from this
tarfile contains the minimal set of files needed for the plugin to work whereas
some extra files will get installed to user systems if the `plugin_dir` uses the
default :file:`build/` directory.

`executables` field
~~~~~~~~~~~~~~~~~~~

The `executables` field is a whitespace-delimited list of shell scripts or
other executables that the package provides. The package manager will make
these executables available inside the user's :file:`bin_dir` directory as
specified in the :ref:`config file <zkg-config-file>`.

An example :file:`zkg.meta`, if the ``Rot13`` example plugin
were also building an executable ``a.out``::

  [package]
  script_dir = scripts/Demo/Rot13
  build_command = ./configure && make
  executables = build/a.out

The package manager makes executables available by maintaining symbolic
links referring from :file:`bin_dir` to the actual files.

Available :program:`since bro-pkg v2.8`.

`build_command` field
~~~~~~~~~~~~~~~~~~~~~

The `build_command` field is an arbitrary shell command that the package
manager will run before installing the package.

This is useful for distributing `Zeek Plugins`_ as source code and having the
package manager take care of building it on the user's machine before installing
the package.

An example :file:`zkg.meta`::

  [package]
  script_dir = scripts/Demo/Rot13
  build_command = ./configure && make

.. note::

   See :ref:`legacy-bro-support` for notes on configuring packages to
   support Bro 2.5 or earlier.

The default CMake skeleton for Zeek plugins will use :file:`build/` as the
directory for the final/built version of the plugin, which matches the defaulted
value of the omitted `plugin_dir` metadata field.

The `script_dir` field is set to the location where the author has placed
custom scripts for their plugin.  When a package has both a Zeek plugin and Zeek
script components, the "plugin" part is always unconditionally loaded by Zeek,
but the "script" components must either be explicitly loaded (e.g. :samp:`@load
{<package_name>}`) or the package marked as :ref:`loaded <load-command>`.

.. _legacy-bro-support:

Supporting Older Bro Versions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Plugin skeletons generated before Bro v2.6 and also any packages
that generally want to support such Bro versions need to pass
an additional configuration option such as::

    build_command = ./configure --bro-dist=%(bro_dist)s && make

See the :ref:`Value Interpolation <metadata-interpolation>`
section for more information on what the ``%(bro_dist)s``
string does, but a brief explanation is that it will expand to
a path containing the Bro source-code on the user's system.
For newer versions of Bro, packages are able to work entirely
with the installation path and don't require original source code.

Also note that other various Zeek scripting and CMake infrastructure may
have changed between Bro v2.6 and Zeek v3.0.  So if you plan to support
older version of Bro (before the Zeek rename), then you should keep an eye
out for various things that got renamed.  For example, the `zeek_init` event
won't exist in any version before Zeek v3.0, nor will any CMake macros
that start with `zeek_plugin`.

.. _metadata-interpolation:

Value Interpolation
^^^^^^^^^^^^^^^^^^^

The `build_command field`_ may reference the settings any given user has in
their customized :ref:`package manager config file <zkg-config-file>`.

For example, if a metadata field's value contains the ``%(bro_dist)s`` string,
then :program:`zkg` operations that use that field will automatically
substitute the actual value of `bro_dist` that the user has in their local
config file.  Note the trailing 's' character at the end of the interpolation
string, ``%(bro_dist)s``, is intended/necessary for all such interpolation
usages.  Note that :program:`since zkg v2.0`, `zeek_dist` is the canonical name
for `bro_dist` within the :ref:`zkg config file <zkg-config-file>`,
but either one means the same thing and should work.  To support older
versions of :program:`bro-pkg`, you'd want to use `bro_dist` in package
metadata files.

Besides the `bro_dist`/`zeek_dist` config keys, any key inside the
`user_vars` sections of their :ref:`package manager config file
<zkg-config-file>` that matches the key of an entry in the package's
`user_vars field`_ will be interpolated.

Another pre-defined config key is `package_base`, which points to the top-level
directory where :program:`zkg` stores all installed packages (i.e.  clones of
each package's git repository). This can be used to gain access to the content
of another package that was installed as a dependency.  Note that
`package_base` is only available :program:`since zkg v2.3`

Internally, the value substitution and metadata parsing is handled by Python's
`configparser interpolation`_.  See its documentation if you're interested in
the details of how the interpolation works.

.. _user-vars:

`user_vars` field
~~~~~~~~~~~~~~~~~

The `user_vars` field is used to solicit feedback from users for use during
execution of the `build_command field`_.

An example :file:`zkg.meta`::

  [package]
  build_command = ./configure --with-librdkafka=%(LIBRDKAFKA_ROOT)s --with-libdub=%(LIBDBUS_ROOT)s && make
  user_vars =
    LIBRDKAFKA_ROOT [/usr] "Path to librdkafka installation"
    LIBDBUS_ROOT [/usr] "Path to libdbus installation"

The format of the field is a sequence entries of the format::

  key [value] "description"

The `key` is the string that should match what you want to be interpolated
within the `build_command field`_.

The `value` is provided as a convenient default value that you'd typically
expect to work for most users.

The `description` is provided as an explanation for what the value will be
used for.

Here's what a typical user would see::

  $ zkg install zeek-test-package
  The following packages will be INSTALLED:
    zeek/jsiwek/zeek-test-package (1.0.5)

  Proceed? [Y/n] y
  zeek/jsiwek/zeek-test-package asks for LIBRDKAFKA_ROOT (Path to librdkafka installation) ? [/usr] /usr/local
  Saved answers to config file: /Users/jon/.zkg/config
  Installed "zeek/jsiwek/zeek-test-package" (master)
  Loaded "zeek/jsiwek/zeek-test-package"

The :program:`zkg` command will iterate over the `user_vars` field of all
packages involved in the operation and prompt the user to provide a value that
will work for their system.

If a user is using the ``--force`` option to :program:`zkg` commands or they
are using the Python API directly, it will first look within the `user_vars`
section of the user's :ref:`package manager config file <zkg-config-file>`
and, if it can't find the key there, it will fallback to use the default value
from the package's metadata.

In any case, the user may choose to supply the value of a `user_vars` key via
an environment variable, in which case, prompts are skipped for any keys
located in the environment. The user may also provide `user_vars` via
``--user-var NAME=VAL`` command-line arguments. These arguments are given
priority over environment variables, which in turn take precedence over any
values in the user's :ref:`package manager config file <zkg-config-file>`.

Available :program:`since bro-pkg v1.1`.

`test_command` field
~~~~~~~~~~~~~~~~~~~~

The `test_command` field is an arbitrary shell command that the package manager
will run when a user either manually runs the :ref:`test command <test-command>`
or before the package is installed or upgraded.

An example :file:`zkg.meta`::

  [package]
  test_command = cd testing && btest -d tests

The recommended test framework for writing package unit tests is `btest`_.
See its documentation for further explanation and examples.

.. note::

   :program:`zkg` version 2.12.0 introduced two improvements to `test_command`:

   - :program:`zkg` now honors package dependencies at test time, meaning that
     if your package depends on another during testing, :program:`zkg` will
     ensure that the dependency is built and available to your package
     tests. Only when all testing succeeds does the full set of new packages
     get installed.

   - The `test_command` now supports value interpolation similarly to the
     `build_command field`_.

`config_files` field
~~~~~~~~~~~~~~~~~~~~

The `config_files` field may be used to specify a list of files that users
are intended to directly modify after installation.  Then, on operations that
would otherwise destroy a user's local modifications to a config file, such
as upgrading to a newer package version, :program:`zkg` can instead save
a backup and possibly prompt the user to review the differences.

An example :file:`zkg.meta`::

  [package]
  script_dir = scripts
  config_files = scripts/foo_config.zeek, scripts/bar_config.zeek

The value of `config_files` is a comma-delimited string of config file paths
that are relative to the root directory of the package.  Config files should
either be located within the `script_dir` or `plugin_dir`.

.. _package-dependencies:

`depends` field
~~~~~~~~~~~~~~~

The `depends` field may be used to specify a list of dependencies that the
package requires.

An example :file:`zkg.meta`::

  [package]
  depends =
    zeek >=2.5.0
    foo *
    https://github.com/zeek/bar >=2.0.0
    package_source/path/bar branch=name_of_git_branch

The field is a list of dependency names and their version requirement
specifications.

A dependency name may be either `zeek`, `zkg`, `bro`, `bro-pkg`,
a full git URL of the package, or a :ref:`package shorthand name
<package-shorthand-name>`.

- The special `zeek` and `bro` dependencies refers not to a package,
  but the version of Zeek that the package requires in order to function.  If
  the user has :program:`zeek-config` or :program:`bro-config` in their
  :envvar:`PATH` when installing/upgrading a package that specifies a `zeek` or
  `bro` dependency, then :program:`zkg` will enforce that the requirement is
  satisfied.

  .. note::

     In this context, `zeek` and `bro` mean the same thing -- the
     later is maintained for backwards compatibility while the former
     became available :program:`since zkg v2.0`.

- The special `zkg` and `bro-pkg` dependencies refers to the version of the
  package manager that is required by the package.  E.g. if a package takes
  advantage of new features that are not present in older versions of the
  package manager, then it should indicate that so users of those old version
  will see an error message an know to upgrade instead of seeing a cryptic
  error/exception, or worse, seeing no errors, but without the desired
  functionality being performed.

  .. note::

     This feature itself, via use of a `bro-pkg` dependency, is only
     available :program:`since bro-pkg v1.2` while a `zkg` dependency is only
     recognized :program:`since zkg v2.0`.  Otherwise, `zkg` and `bro-pkg` mean
     the same thing in this context.

- The full git URL may be directly specified in the `depends` metadata if you
  want to force the dependency to always resolve to a single, canonical git
  repository.  Typically this is the safe approach to take when listing
  package dependencies and for publicly visible packages.

- When using shorthand package dependency names, the user's :program:`zkg`
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

Note that these specifications are strict semantic versions.  Even if a
given package chooses to use the ``vX.Y.Z`` format for its :ref:`git
version tags <package-versioning>`, do not use the 'v' prefix in the
version specifications here as that is not part of the semantic version.

`external_depends` field
~~~~~~~~~~~~~~~~~~~~~~~~

The `external_depends` field follows the same format as the
:ref:`depends field <package-dependencies>`, but the dependency names refer
to external/third-party software packages.  E.g. these would be set to typical
package names you'd expect the package manager from any given operating system
to use, like 'libpng-dev'.  The version specification should also generally
be given in terms of semantic versioning where possible.  In any case, the
name and version specification for an external dependency are only used
for display purposes -- to help users understand extra pre-requisites
that are needed for proceeding with package installation/upgrades.

Available :program:`since bro-pkg v1.1`.

`suggests` field
~~~~~~~~~~~~~~~~

The `suggests` field follows the same format as the :ref:`depends field
<package-dependencies>`, but it's used for specifying optional packages that
users may want to additionally install.  This is helpful for suggesting
complementary packages that aren't strictly required for the suggesting package
to function properly.

A package in `suggests` is functionaly equivalent to a package in `depends`
except in the way it's presented to users in various prompts during
:program:`zkg` operations.  Users also have the option to ignore
suggestions by supplying an additional ``--nosuggestions`` flag to
:program:`zkg` commands.

Available :program:`since bro-pkg v1.3`.

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

The tag name may also be of the ``vX.Y.Z`` form (prefixed by 'v').
Choose whichever you prefer.

Then, assuming you've already set up a public/remote git repository
(e.g. on GitHub) for your package, remember to push the tag to the
remote repository:

   .. code-block:: console

      $ git push --tags

Alternatively, if you expect to have a simple development process for
your package, you may choose to not create any version tags and just
always make commits directly to your package's default branch (typically named
*main* or *master*).  Users will receive package updates differently depending
on whether you decide to use release version tags or not.  See the
:ref:`package upgrade process <package-upgrade-process>` documentation for more
details on the differences.

.. _package-upgrade-process:

Package Upgrade Process
~~~~~~~~~~~~~~~~~~~~~~~

The :ref:`install command <install-command>` will either install a
stable release version or the latest commit on a specific git branch of
a package.

The default installation behavior of :program:`zkg` is to look for
the latest release version tag and install that.  If there are no such
version tags, it will fall back to installing the latest commit of the
package's default branch (typically named *main* or *master*)

Upon installing a package via a :ref:`git version tag
<package-versioning>`, the :ref:`upgrade command <upgrade-command>` will
only upgrade the local installation of that package if a greater version
tag is available.  In other words, you only receive stable release
upgrades for packages installed in this way.

Upon installing a package via a git branch name, the :ref:`upgrade
command <upgrade-command>` will upgrade the local installation of the
package whenever a new commit becomes available at the end of the
branch.  This method of tracking packages is suitable for testing out
development/experimental versions of packages.

If a package was installed via a specific commit hash, then the package
will never be eligible for automatic upgrades.
