#!/usr/bin/env python3

from setuptools import setup
import tenzing

setup(name='sunyata',
      version='0.1.1',
      description='APIGateway-Lambda deployment tools.',
      author='Steve Norum',
      author_email='stevenorum@gmail.com',
      url='www.stevenorum.com',
      packages=['sunyata'],
      package_dir={'sunyata': 'sunyata'},
      scripts=['scripts/sunyata'],
      test_suite='tests',
      cmdclass = {'upload':tenzing.Upload}
)
