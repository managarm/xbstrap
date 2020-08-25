# SPDX-License-Identifier: MIT

import re

def open_file_from_cli(spec, *args, **kwargs):
	m = re.match(r'fd:(\d+)', spec)
	if m is not None:
		return open(int(m.group(1)), *args, **kwargs)
	m = re.match(r'path:(.+)', spec)
	if m is not None:
		return open(m.group(1), *args, **kwargs)
	raise ValueError('Illegal file specification on CLI')
