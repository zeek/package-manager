from setuptools import setup

setup(
    name='bro-pkg',
    version=open('VERSION').read().replace('-', '.dev', 1).strip(),
    description='The Bro Package Manager',
    long_description=open('README').read(),
    license='University of Illinois/NCSA Open Source License',
    keywords='bro broctl brocontrol package manager scripts plugins security',
    maintainer='The Bro Project',
    maintainer_email='info@bro.org',
    url='https://github.com/bro/package-manager',
    scripts=['bro-pkg'],
    packages=['bropkg'],
    install_requires=['gitpython', 'semantic_version'],
    classifiers=[
        'Development Status :: 3 - Alpha',
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
