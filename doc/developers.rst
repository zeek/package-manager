.. _Sphinx: http://www.sphinx-doc.org
.. _Read the Docs: https://docs.zeek.org/projects/package-manager
.. _GitHub: https://github.com/zeek/package-manager
.. _Google Style Docstrings: http://sphinxcontrib-napoleon.readthedocs.io/en/latest/example_google.html
.. _zeek-aux: https://github.com/zeek/zeek-aux
.. _PyPi: https://pypi.python.org/pypi

Developer's Guide
=================

This a guide for developers working on the Zeek Package Manager itself.

Versioning/Releases
-------------------

After making a commit to the *master* branch, you can use the
:program:`update-changes` script in the `zeek-aux`_ repository to automatically
adapt version numbers and regenerate the :program:`zkg` man page.  Make sure
to install the `documentation dependencies`_ before using it.

Releases are hosted at PyPi_.  To build and upload a release:

#. Finalize the git repo tag and version with  ``update-changes -R <version>``
   if not done already.

#. Upload the distribution (you will need the credentials for the 'zeek'
   account on PyPi):

   .. code-block:: console

      $ make upload

Documentation
-------------

Documentation is written in reStructuredText (reST), which Sphinx_ uses to
generate HTML documentation and a man page.

.. _documentation dependencies:

Dependencies
~~~~~~~~~~~~

To build documentation locally, find the requirements in
:file:`requirements.txt`:

  .. literalinclude:: ../requirements.txt

They can be installed like::

  pip3 install -r requirements.txt

Local Build/Preview
~~~~~~~~~~~~~~~~~~~

Use the :file:`Makefile` targets ``make html`` and ``make man`` to build the
HTML and man page, respectively, or ``make doc`` to build them both.  To view
the generated HTML output, open :file:`doc/_build/index.html`.  The generated
man page is located in :file:`doc/man/zkg.1`.

If you have also installed :program:`sphinx-autobuild` (e.g. via
:program:`pip3`), there's a :file:`Makefile` target, ``make livehtml``, you can
use to help preview documentation changes as you edit the reST files.

Remote Hosting
~~~~~~~~~~~~~~

The GitHub_ repository has a webhook configured to automatically rebuild the
HTML documentation hosted at `Read the Docs`_ whenever a commit is pushed.

Style Conventions
~~~~~~~~~~~~~~~~~

The following style conventions are (generally) used.

========================== =============================== ===========================
Documentation Subject      reST Markup                     Preview
========================== =============================== ===========================
File Path                  ``:file:`path```                :file:`path`
File Path w/ Substitution  ``:file:`{<replace_me>}/path``` :file:`{<replace_me>}/path`
OS-Level Commands          ``:command:`cmd```              :command:`cmd`
Program Names              ``:program:`prog```             :program:`prog`
Environment Variables      ``:envvar:`VAR```               :envvar:`VAR`
Literal Text (e.g. code)   ````code````                    ``code``
Substituted Literal Text   ``:samp:`code {<replace_me>}``` :samp:`code {<replace_me>}`
Variable/Type Name         ```x```                         `x`
INI File Option            ```name```                      `name`
========================== =============================== ===========================

Python API docstrings roughly follow the `Google Style Docstrings`_ format.

Internals
---------

``zkg``'s view of a package
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``zkg`` maintains copies of a Zeek package in up to four places:

- A long-lived clone of the package's git repo in
  ``$STATEDIR/clones/package/<name>``. This clone (not its installed version,
  see below) is ``zkg``'s "authoritative" view of a package.

- A conceptually short-lived clone in ``$STATEDIR/scratch/<name>``, for
  retrieving information about a package ("short-lived", because access to those
  copies is momentary -- not because ``zkg`` necessarily cleans up after those
  copies).

- A "stage". A stage is any place in which ``zkg`` actually installs a package
  for running in Zeek. This can be the local Zeek installation, or locally below
  ``$STATEDIR/testing`` when testing a package. So "staging" here does not mean
  "not yet live"; rather the opposite: it's closer to "any place where the
  package may actually run". A stage allows customization of many of the
  directories involved (for internal cloning, installing Zeek scripts and
  plugins, binary files, etc). Installation into a stage is also where ``zkg``
  adds its management of ``packages.zeek``, in the stage's scripts directory.

- In ``$STATEDIR/testing/<name>/clones/<names>``, for testing a given package
  along with any dependencies it might have.

``zkg`` captures state about installed packages in ``$STATEDIR/manifest.json``.
This does not capture all knowable information about packages, though.
More on this next.

Directory usage
~~~~~~~~~~~~~~~

``zkg`` populates its internal state directory (dubbed ``$STATEDIR`` below) with
several subdirectories.

``$STATEDIR/clones``
""""""""""""""""""""

This directory keeps git clones of installed packages
(``$STATEDIR/clones/package/<name>``), packages sources
(``$STATEDIR/clones/source/<name>``), and package template repositories
(``$STATEDIR/clones/template/<name>``).

``zkg`` clones the relevant repositories as needed. It can dynamically re-create
some of these directories as needed, but interfering in that space is not
recommended. For example, if you remove a clone of an installed package, the
installation itself will remain live (via the staging mechanism), but ``zkg``
will no longer be able to refer to all information about the installed package
(because anything not explicitly captured about the package in
``$STATEDIR/manifest.json`` is now gone).

Removal of a package (``zkg remove``) removes its clone in ``$STATEDIR/clones``.

``$STATEDIR/scratch``
"""""""""""""""""""""

When retrieving information on a package that isn't yet installed, or where
``zkg`` doesn't want to touch the installed code, ``$STATEDIR/scratch/<name>``
is a clone of the package's git repo at a version (often a git tag) of
interest. This clone is shallow for any versions that aren't raw SHA-1
hashes. The information parsed includes the ``zkg.meta`` as well as git
branch/tag/commit info.

During package installation, ``zkg`` places backups of user-tweakable files into
``$STATEDIR/scratch/tmpcfg``. ``zkg`` restores these after package installation
to preserve the user's edits. During package source aggregation, ``zkg`` places
temporary versions of ``aggregate.meta`` directly into ``$STATEDIR/scratch``.

Creation or unbundling of a package happens via ``$STATEDIR/scratch/bundle``, to
compose or retrieve information about the bundle. The directory is deleted and
re-created at the beginning of those operations:

- During bundling, ``zkg`` copies installed package repos from
  ``$STATEDIR/clones/<name>`` into ``$STATEDIR/scratch/bundle/<name>``, and
  creates fresh git clones in the ``bundle`` directory for any packages not
  currently installed. It creates a ``.tar.gz`` of the whole directory,
  initially in the bundle directory, and moves it to where the user specified.

- During unbundling, ``zkg`` reads the bundle manifest as well as the git repos
  of the contained packages, and moves the package repos from the scratch space
  into ``$STATEDIR/clones/package/<name>``.

When installing a package's ``script_dir`` or ``plugin_dir`` into a staging area
and the source file is a tarfile, ``zkg`` temporarily extracts the tarball into
``$STATEDIR/scratch/untar/``.

There's little or no cleanup of files in the scratch space after the operations
creating them complete. ``zkg`` only deletes, then (re-)creates, the directories
involved upon next use.

When ``zkg`` isn't in the middle of executing any commands, you can always
delete the scratch space without negatively affecting ``zkg``.

``$STATEDIR/testing``
"""""""""""""""""""""

When testing a package (during installation, or when explicitly running ``zkg
test``), ``zkg`` creates a staging area ``$STATEDIR/testing/<name>`` for the
package under test, clones the package and its dependencies into
``$STATEDIR/testing/<name>/clones/``, installs them from there into
``$STATEDIR/testing/<name>``, and then runs the package's ``test_command`` from
its clone (``$STATEDIR/testing/<name>/clones/<name>``), with an environment set
such that it finds the installation in the local stage. The stdout and stderr of
those testing runs is preserved into ``zkg.test_command.stdout`` and
``zkg.test_command.stderr`` in that directory.

As in ``STATEDIR/scratch``, there's no cleanup, and you can delete the testing
space as needed after a test run is complete.
