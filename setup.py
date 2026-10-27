import pathlib

from setuptools import setup


def version() -> str:
    return pathlib.Path("VERSION").read_text().replace("-", ".dev", 1).strip()


setup(
    version=version(),
)
