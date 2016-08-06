Anatomy of a Package Source
===========================

:ref:`bro-pkg <bro-pkg>`, by default, is configured to obtain packages from a
single "package source": the `Bro Packages Git Repository`_, which is hosted by
and loosely curated by the Bro Team. However, users may :ref:`configure bro-pkg
<bro-pkg-config-file>` to use other package sources: either ones they've set up
themselves for organization purposes or those hosted by other third parties.

Package Source Setup
--------------------

In order to set up such a package source, one simply has to create a git
repository and then add `git submodules`_ to it, with those submodules being
:doc:`Bro Packages <package>`.  The submodules can be created at any path in
the package source's git repository.  E.g. the `Bro Packages Git Repository`_
organizes submodules hierarchically at paths based on GitHub usernames such as
"alice/foo" or "bob/bar" where "alice" and "bob" are usernames and "foo" and
"bar" are the names of their respective packages.  However, a source is free
to use a flat organization for submodule paths if desired.

After creating a git repo for the package source and adding submodules to it,
it's ready to be used by :ref:`bro-pkg <bro-pkg>`.

Package Source Maintenance
--------------------------

Once added to a source, package submodules do not have to be updated at all.
:ref:`bro-pkg <bro-pkg>` may use the git URL referenced by the submodule to
interact directly with the Package's git repository in order to fetch updated
versions.

Adding Packages
~~~~~~~~~~~~~~~

Just add a new git submodule to the package source's git repository that points
to the git URL of the Bro Package.  :ref:`bro-pkg <bro-pkg>` will see the new
package listed the next time it uses the
:ref:`refresh command <refresh-command>`.

Removing Packages
~~~~~~~~~~~~~~~~~

Just remove the Package's git submodule from the package source's git
repository.  :ref:`bro-pkg <bro-pkg>` will no longer see the now-removed package
listed by the :ref:`list command <list-command>` after the next time they use
the :ref:`refresh command <refresh-command>`.

Users that had previously installed the now-removed package may continue to
use it and receive updates for it -- packages not tied to any package source are
allowed to be installed if the user refers to the package by a full git URL
instead of the convienient/shorter submodule path/name that would be available
if it were a part of a package source.

.. _Bro Packages Git Repository: https://github.com/bro/packages
.. _git submodules: https://git-scm.com/docs/git-submodule
