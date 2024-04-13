import pathlib

from setuptools import setup


def version():
    return pathlib.Path("VERSION").read_text().replace("-", ".dev", 1).strip()


setup(
    version=version(),
    data_files=[
        ("output_dir", ["VERSION"]),
    ],
)
