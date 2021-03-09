# SPDX-License-Identifier: MIT

import errno
import os
import urllib.parse
import urllib.request
import sys

import colorama

def log_info(msg):
	print('{}xbstrap{}: {}'.format(colorama.Style.BRIGHT, colorama.Style.RESET_ALL, msg))

def log_warn(msg):
	print("{}xbstrap{}: {}{}{}".format(colorama.Style.BRIGHT, colorama.Style.NORMAL, colorama.Fore.YELLOW, msg, colorama.Style.RESET_ALL), file=sys.stderr)

def log_err(msg):
	print("{}xbstrap{}: {}{}{}".format(colorama.Style.BRIGHT, colorama.Style.NORMAL, colorama.Fore.RED, msg, colorama.Style.RESET_ALL), file=sys.stderr)

def try_mkdir(path, recursive=False):
	try:
		if not recursive:
			os.mkdir(path)
		else:
			os.makedirs(path)
	except OSError as e:
		if e.errno != errno.EEXIST:
			raise

def build_environ_paths(environ, varname, prepend):
	if not prepend:
		return
	joined = ':'.join(prepend)
	if varname in environ and environ[varname]:
		environ[varname] = joined + ':' + environ[varname]
	else:
		environ[varname] = joined

def interactive_download(url, path):
	print('...', end='') # This will become the status line.

	def show_progress(num_blocks, block_size, file_size):
		progress = min(num_blocks * block_size, file_size)
		print('\r\x1b[K{:8.0f} KiB / {:8.0f} KiB, {:7.2f}%'.format(progress / 1024,
				file_size / 1024,
				progress / file_size * 100), end='')

	temp_path = path + '.download'
	urllib.request.urlretrieve(url, temp_path, show_progress)
	os.rename(temp_path, path)
	print()
