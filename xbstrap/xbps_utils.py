import tarfile

import plistlib
import shlex
import zstandard

def read_repodata(path):
	with open(path, "rb") as zidx:
		dctx = zstandard.ZstdDecompressor()
		with dctx.stream_reader(zidx) as reader:
			with tarfile.open(fileobj=reader, mode="r|") as tar:
				for ent in tar:
					if ent.name != "index.plist":
						continue
					with tar.extractfile(ent) as idxpl:
						pkg_idx = plistlib.load(idxpl, fmt=plistlib.FMT_XML)
						return pkg_idx

# XBPS INSTALL/REMOVE scripts are called with arguments:
# <action> <pkgname> <version> <update yes/no> "no" <arch>

def compose_xbps_install(cfg, pkg):
	yml = pkg._this_yml.get('scripts', dict()).get('post_install')
	if not yml:
		return None

	step_sh = []
	for step_yml in yml:
		args_yml = step_yml['args']
		if isinstance(args_yml, str):
			step_sh.append('(eval ' + shlex.quote(args_yml) + ')')
		else:
			step_sh.append('(exec `which ' + shlex.quote(args_yml[0]) + '`'
				+ ''.join(' ' + shlex.quote(q) for q in args_yml[1:]) + ')')

	return (
		'#!/bin/sh\n'
		'case "$1" in\n'
		'post)\n'
		+ ''.join(f'    {s}\n' for s in step_sh) +
		'    ;;\n'
		'esac\n')
