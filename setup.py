from setuptools import setup

setup(
    name='bro-pkg',
    version=open('VERSION').read(),
    description='The Bro Package Manager',
    long_description=open('README').read(),
    license='University of Illinois/NCSA Open Source License',
    keywords='bro broctl brocontrol package manager scripts plugins security',
    maintainer='The Bro Team',
    maintainer_email='info@bro.org',
    url='https://github.com/bro/package-manager',
    scripts=['bro-pkg'],
    packages=['bropkg'],
    install_requires=['gitpython', 'semantic_version'],
)
