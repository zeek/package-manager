from setuptools import setup
import os

install_requires = ['gitpython', 'semantic_version', 'btest']

setup(
    name='bro-pkg' if os.environ.get('ZKG_PYPI_DIST_LEGACY') else 'zkg',
    version=open('VERSION').read().replace('-', '.dev', 1).strip(),
    description='The Zeek Package Manager',
    long_description=open('README').read(),
    license='University of Illinois/NCSA Open Source License',
    keywords='zeek bro zeekctl zeekcontrol broctl brocontrol package manager scripts plugins security',
    maintainer='The Zeek Project',
    maintainer_email='info@zeek.org',
    url='https://github.com/zeek/package-manager',
    scripts=['bro-pkg', 'zkg'],
    packages=['bropkg', 'zeekpkg'],
    install_requires=install_requires,
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'License :: OSI Approved :: University of Illinois/NCSA Open Source License',
        'Operating System :: POSIX :: Linux',
        'Operating System :: MacOS :: MacOS X',
        'Programming Language :: Python :: 3',
        'Topic :: System :: Networking :: Monitoring',
        'Topic :: Utilities',
    ],
)
