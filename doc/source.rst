.. _Zeek Packages Git Repository: https://github.com/zeek/packages

How-To: Create a Package Source
===============================

:ref:`zkg <zkg>`, by default, is configured to obtain packages from a
single "package source", the `Zeek Packages Git Repository`_, which is hosted by
and loosely curated by the Zeek Team. However, users may :ref:`configure zkg
<zkg-config-file>` to use other package sources: either ones they've set up
themselves for organization purposes or those hosted by other third parties.

Package Source Setup
--------------------

In order to set up such a package source, one simply has to create a git
repository and then add :ref:`Package Index Files <package-index-file>` to it.
These files may be created at any path in the package source's git repository.
E.g. the `Zeek Packages Git Repository`_ organizes package index files
hierarchically based on package author names such as :file:`alice/zkg.index`
or :file:`bob/zkg.index` where ``alice`` and ``bob`` are usually GitHub
usernames or some unique way of identifying the organization/person that
maintains Zeek packages.  However, a source is free to use a flat organization
with a single, top-level :file:`zkg.index`.

.. note::

   The magic index file name of :file:`zkg.index` is available :program:`since
   zkg v2.0`.  For compatibility purposes, the old index file name of
   :file:`bro-pkg.index` is also still supported.

After creating a git repo for the package source and adding package index files
to it, it's ready to be used by :ref:`zkg <zkg>`.

.. _package-index-file:

Package Index Files
-------------------

Files named :file:`zkg.index` (or the legacy :file:`bro-pkg.index`) are used to
describe the :doc:`Zeek Packages <package>` found within the package source.
They are simply a list of git URLs pointing to the git repositories of
packages.  For example::

  https://github.com/zeek/foo
  https://github.com/zeek/bar
  https://github.com/zeek/baz

Local filesystem paths are also valid if the package source is only meant for
your own private usage or testing.

Adding Packages
---------------

Adding packages is as simple as adding new :ref:`Package Index Files
<package-index-file>` or extending existing ones with new URLs and then
commiting/pushing those changes to the package source git repository.

:ref:`zkg <zkg>` will see new packages listed the next time it uses
the :ref:`refresh command <refresh-command>`.

Removing Packages
-----------------

Just remove the package's URL from the :ref:`Package Index File
<package-index-file>` that it's contained within.

After the next time :program:`zkg` uses the :ref:`refresh command
<refresh-command>`, it will no longer see the now-removed package
when viewing package listings via by the :ref:`list command <list-command>`.

Users that had previously installed the now-removed package may continue to
use it and receive updates for it.

Aggregating Metadata
--------------------

The maintainer/operator of a package source may choose to periodically aggregate
the metadata contained in its packages' :file:`zkg.meta` (and legacy
:file:`bro-pkg.meta`) files.  The :ref:`zkg refresh <refresh-command>`
is used to perform the task.  For example:

.. code-block:: console

  $ zkg refresh --aggregate --push --sources my_source

The optional ``--push`` flag is helpful for setting up cron jobs to
automatically perform this task periodically, assuming you've set up your
git configuration to push changesets without interactive prompts.  E.g.
to set up pushing to remote servers you could set up SSH public key
authentication.

Aggregated metadata gets written to a file named :file:`aggregate.meta`
at the top-level of a package source and the :ref:`list <list-command>`,
:ref:`search <search-command>`, and :ref:`info <info-command>` all may access
this file.  Having access to the aggregated metadata in this way
is beneficial to all :program:`zkg` users because they then will not have
to crawl the set of packages listed in a source in order to obtain this metadata
as it will have already been pre-aggregated by the operator of the package
source.
