#!/usr/bin/python3

import os
import shutil
from setuptools import setup
from setuptools.command.develop import develop
from setuptools.command.install import install

with open('README.md', 'r') as f:
	readme = f.read()

class CompletionDevelop(develop):
	def run(self):
		if(os.access('/etc/bash_completion.d', os.W_OK)):
			shutil.copyfile('extrafiles/completion.sh', '/etc/bash_completion.d/xbstrap')
		else:
			print('Insufficient permissions to install the completion script to /etc/bash_completion.d')
		develop.run(self)

class CompletionInstall(install):
	def run(self):
		if(os.access('/etc/bash_completion.d', os.W_OK)):
			shutil.copyfile('extrafiles/completion.sh', '/etc/bash_completion.d/xbstrap')
		else:
			print('Insufficient permissions to install the completion script to /etc/bash_completion.d')
		install.run(self)

setup(name='xbstrap',
	version='0.24',
	packages=['xbstrap'],
	package_data={
		'xbstrap': ['schema.yml']
	},
	scripts=['scripts/xbstrap', 'scripts/xbstrap-mirror', 'scripts/xbstrap-pipeline'],
	install_requires=[
		'colorama',
		'jsonschema',
		'pyyaml',
		'zstandard' # For xbps support.
	],
	cmdclass={
		'develop': CompletionDevelop,
		'install': CompletionInstall,
	},

	# Package metadata.
	author='Alexander van der Grinten',
	author_email='alexander.vandergrinten@gmail.com',
	license='MIT',
	url='https://github.com/managarm/xbstrap',
	long_description=readme,
	long_description_content_type='text/markdown'
)

