Bro Package Manager
===================

:warning: This project is still under development and so may not yet be suitable
          for use in production environments.

The Bro Package Manager makes it easy for Bro users to install and manage third
party scripts as well as plugins for Bro and BroControl.  The Bro Package
Manager command line client, called `bro-pkg`, is preconfigured to download
packages from a GitHub repository that has been set up such that any developer
can request their Bro package be included.  This repository is located at:

    https://github.com/bro/packages

See the README file of that repository for information on the package submission
process.

Dependencies
------------

For users of the command-line client or Python API:

* Python 2.7+
* GitPython: https://pypi.python.org/pypi/GitPython
* semantic_version: https://pypi.python.org/pypi/semantic_version

For developers or those that want to build the documentation locally:

* Sphinx: https://pypi.python.org/pypi/Sphinx

Installation
------------

If installed as part of a Bro installation, the `bro-pkg` command line client
should be preconfigured and ready to use.  If you want to customize the paths
or settings it uses, just edit the `bro-pkg.config` which also got installed
with it.

Installation via `setuptools`, `pip`, etc is also possible, but additional
configuration after installation is likely required.  See the example
`bro-pkg.config` file for an explanation of what can be configured, but the
suggested/minimum configuration would be to use the following settings:

- set `scriptdir`, to the location of Bro's "site" scripts directory (e.g.
  `$bro_install_prefix/share/bro/site`)

- set `plugindir`, to the location of Bro's default plugin directory (e.g.
  `$bro_install_prefix/lib/bro/plugins`)

- set `bro_dist`, if you plan on installing packages that have Bro plugins
  and require compilation

With those settings, the package manager will install Bro scripts, Bro plugins,
and BroControl plugins into directories where `bro`/`broctl` expect to find such
things by default.  BroControl also needs no further configuration to
automatically distribute installed package scripts/plugins to all nodes, but
you need to add "@load packages" to your `site/local.bro` if you want it to
have Bro load all the scripts from installed packages (that are marked as
"loaded").  Alternatively, you could add "@load <package_name>" for individual,
installed packages.

If you prefer to not use those settings for `scriptdir` and `plugindir`, the
default `bro-pkg` configuration will install all package scripts/plugins within
`$HOME/.bro-pkg`.  To get command-line `bro` to be aware of Bro scripts/plugins
in that location, you may want to set the `bro_exe` config option and use
`bro-pkg env` to help you set up your environment.  To get `broctl` to be aware
of scripts/plugins in that location, you may want to edit `broctl.cfg` and
adjust `SitePolicyPath` and `SitePluginPath` according to the output of
`bro-pkg config scriptdir` and `bro-pkg config plugindir`, respectively.
