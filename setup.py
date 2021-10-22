from setuptools import setup

setup(
    name='discord_gateway',
    version='0.0.1',
    description='Sans-I/O implementation of the Discord gateway.',
    packages=['discord_gateway'],
    install_requires=['wsproto'],
    extras_require={'etf': ['erlpack'], 'perf': ['ujson']},
)
