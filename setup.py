from setuptools import setup
from sys import version_info
import os

install_requires = ['gitpython', 'semantic_version', 'btest']

if version_info[0] + version_info[1] / 10 < 3.5 or os.environ.get('BROPKG_PYPI_DIST'):
    install_requires.append("configparser")

setup(
    name='bro-pkg',
    version=open('VERSION').read().replace('-', '.dev', 1).strip(),
    description='The Zeek Package Manager',
    long_description=open('README').read(),
    license='University of Illinois/NCSA Open Source License',
    keywords='zeek bro broctl brocontrol package manager scripts plugins security',
    maintainer='The Zeek Project',
    maintainer_email='info@zeek.org',
    url='https://github.com/bro/package-manager',
    scripts=['bro-pkg'],
    packages=['bropkg'],
    install_requires=install_requires,
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'License :: OSI Approved :: University of Illinois/NCSA Open Source License',
        'Operating System :: POSIX :: Linux',
        'Operating System :: MacOS :: MacOS X',
        'Programming Language :: Python :: 2.7',
        'Programming Language :: Python :: 3',
        'Topic :: System :: Networking :: Monitoring',
        'Topic :: Utilities',
    ],
)
