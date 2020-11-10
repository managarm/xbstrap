# SPDX-License-Identifier: MIT

import os
import urllib.parse
import urllib.request

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
