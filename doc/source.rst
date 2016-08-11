.. _Bro Packages Git Repository: https://github.com/bro/packages

How-To: Create a Package Source
===============================

:ref:`bro-pkg <bro-pkg>`, by default, is configured to obtain packages from a
single "package source", the `Bro Packages Git Repository`_, which is hosted by
and loosely curated by the Bro Team. However, users may :ref:`configure bro-pkg
<bro-pkg-config-file>` to use other package sources: either ones they've set up
themselves for organization purposes or those hosted by other third parties.

Package Source Setup
--------------------

In order to set up such a package source, one simply has to create a git
repository and then add :ref:`Package Index Files <package-index-file>` to it.
These files may be created at any path in the package source's git repository.
E.g. the `Bro Packages Git Repository`_ organizes package index files
hierarchically based on package author names such as :file:`alice/bro-pkg.index`
or :file:`bob/bro-pkg.index` where ``alice`` and ``bob`` are usually GitHub
usernames or some unique way of identifying the organization/person that
maintains Bro packages.  However, a source is free to use a flat organization
with a single, top-level :file:`bro-pkg.index`.

After creating a git repo for the package source and adding package index files
to it, it's ready to be used by :ref:`bro-pkg <bro-pkg>`.

.. _package-index-file:

Package Index Files
-------------------

Files named :file:`bro-pkg.index` are used to describe the :doc:`Bro Packages
<package>` found within the package source. They use a simple INI format, for
example::

  [foo]
  url = https://github.com/bro/foo
  tags = example, bro plugin, pity

  [bar]
  url = https://github.com/bro/bar
  tags = example, broctl plugin, pub

  [baz]
  url = https://github.com/bro/baz
  tags = example, storm

Each section of the file, e.g. ``[foo]``, describes a package.  The logical
choice to use for these section names is the last component of the git URL as
that's the shorthand way to refer to the packages when using
:ref:`bro-pkg <bro-pkg>`.

The `url` field may be set to the URL of any valid git repository.  This
includes local paths, though that's not a good choice for package sources that
are meant to be shared with others.

The `tags` field contains a comma-delimited set of metadata tags that further
classify and describe the purpose of the package.  This is used to help users
better discover and search for packages.  E.g. the
:ref:`bro-pkg search <search-command>` command will inspect these tags.

Adding Packages
---------------

Adding packages is as simple as adding new :ref:`Package Index Files
<package-index-file>` or extending existing ones with new sections and
commiting/pushing those changes to the package source git repository.

:ref:`bro-pkg <bro-pkg>` will see new packages listed the next time it uses
the :ref:`refresh command <refresh-command>`.

Removing Packages
-----------------

Just remove the package's section from the :ref:`Package Index File
<package-index-file>` that it's contained within.

After the next time :program:`bro-pkg` uses the :ref:`refresh command
<refresh-command>`, it will no longer see the now-removed package
when viewing package listings via by the :ref:`list command <list-command>`.

Users that had previously installed the now-removed package may continue to
use it and receive updates for it.
