#!/usr/bin/env python

import pip
from pkg_resources import parse_version
from setuptools import setup, find_packages

install_requires = [
    'biopython>=1.7.4',
    'flashtext>=2.7',
    'pysbol>=2.3.1'
]

setup(
    name='SYNBICT',
    version='1.1',
    packages=find_packages(),
    install_requires=install_requires,
    classifiers=[
        "Programming Language :: Python :: 3 :: Only"
    ]
)
