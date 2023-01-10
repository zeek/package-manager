import pathlib
from setuptools import setup

install_requires = ["gitpython", "semantic_version", "btest"]


def version():
    return pathlib.Path("VERSION").read_text().replace("-", ".dev", 1).strip()


def long_description():
    return pathlib.Path("README").read_text()


setup(
    name="zkg",
    version=version(),
    description="The Zeek Package Manager",
    long_description=long_description(),
    license="University of Illinois/NCSA Open Source License",
    keywords="zeek zeekctl zeekcontrol package manager scripts plugins security",
    maintainer="The Zeek Project",
    maintainer_email="info@zeek.org",
    url="https://github.com/zeek/package-manager",
    scripts=["zkg"],
    packages=["zeekpkg"],
    install_requires=install_requires,
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Console",
        "License :: OSI Approved :: University of Illinois/NCSA Open Source License",
        "Operating System :: POSIX :: Linux",
        "Operating System :: MacOS :: MacOS X",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Networking :: Monitoring",
        "Topic :: Utilities",
    ],
)
