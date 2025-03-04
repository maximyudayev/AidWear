"""Vicon DSSDK PyPI setup."""

import os
from setuptools import find_packages, setup

_THIS_DIR = os.path.abspath(os.path.dirname(__file__))

def read_file_contents(file_path):
    '''Reads the contents of a file returning it as a string.'''

    try:
        with open(file_path) as file_:
            return file_.read()
    except: #pylint: disable=W0702
        print( 'Failed to read [' + file_path + ']' )
        return ''

README = read_file_contents(os.path.join(_THIS_DIR, 'README.rst'))

CHANGES = read_file_contents(os.path.join(_THIS_DIR, 'CHANGES.rst'))

VERSION = read_file_contents(os.path.join(_THIS_DIR, 'VERSION')).strip()

setup(name='vicon_dssdk',
      version=VERSION,
      packages=find_packages(),
      include_package_data=True,
      install_requires=['enum34'],
      description='Vicon DataStream SDK.',
      long_description=README + '\n\n' + CHANGES,
      long_description_content_type='text/x-rst',
      author='Vicon Motion Systems Ltd',
      author_email='support@vicon.com',
      url='https://vicon.com/support',
      classifiers=(
          'Programming Language :: Python :: 2',
          'Programming Language :: Python :: 3',
          'License :: OSI Approved :: MIT License',
          'Operating System :: Microsoft :: Windows'),
     )
