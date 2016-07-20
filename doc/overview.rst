Bro Package Manager
===================

:warning: This project is still under development and so may not yet be suitable
          for use in production environments.

The Bro Package Manager makes it easy for Bro users to install and manage third
party scripts as well as plugins for Bro and BroControl.  The Bro Package
Manager command line client, called 'bro-pkg', is preconfigured to download
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

* Sphinx: https://pypi.python.org/pypi/Sphinx/1.4.5
* sphinx-argparse: https://pypi.python.org/pypi/sphinx-argparse

Installation
------------
@todo: installing with Bro

@todo: standalone install
