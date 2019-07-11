#!/usr/bin/python3

from setuptools import setup

setup(name='xbstrap',
	version='0.8',
	scripts=['scripts/xbstrap'],
	install_requires=[
		'colorama',
		'pyyaml'
	],
	data_files=[
        ('/etc/bash_completion.d', ['scripts/completion.sh']),
    ],

	# Package metadata.
	author='Alexander van der Grinten',
	author_email='alexander.vandergrinten@gmail.com',
	license='MIT',
	url='https://github.com/managarm/xbstrap'
)

