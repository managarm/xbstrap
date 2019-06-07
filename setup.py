#!/usr/bin/python3

from setuptools import setup

setup(name='xbstrap',
	version='0.6',
	scripts=['scripts/xbstrap'],
	install_requires=[
		'colorama',
		'pyyaml'
	],

	# Package metadata.
	author='Alexander van der Grinten',
	author_email='alexander.vandergrinten@gmail.com',
	license='MIT',
	url='https://github.com/managarm/xbstrap'
)

