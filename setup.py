#!/usr/bin/env python3

from setuptools import setup

setup(name='sunyata',
      version='0.1.0',
      description='APIGateway-Lambda deployment tools.',
      author='Steve Norum',
      author_email='stevenorum@gmail.com',
      url='www.stevenorum.com',
      packages=['sunyata'],
      package_dir={'sunyata': 'sunyata'},
      scripts=['scripts/sunyata'],
      test_suite='tests',
     )
