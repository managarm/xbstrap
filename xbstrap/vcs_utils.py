# SPDX-License-Identifier: MIT

import os
import shutil
import subprocess
import urllib.request

from . import util as _util

def fetch_repo(cfg, src):
	source = src._this_yml

	if 'git' in source:
		git = shutil.which('git')
		if git is None:
			raise GenericException("git not found; please install it and retry")
		commit_yml = cfg._commit_yml.get('commits', dict()).get(src.name, dict())
		fixed_commit = commit_yml.get('fixed_commit', None)

		init = not os.path.isdir(src.source_dir)
		if init:
			_util.try_mkdir(src.source_dir)
			subprocess.check_call([git, 'init'], cwd=src.source_dir)
			subprocess.check_call([git, 'remote', 'add', 'origin', source['git']],
					cwd=src.source_dir)

		shallow = not source.get('disable_shallow_fetch', False)
		# We have to disable shallow fetches to get rolling versions right.
		if src.is_rolling_version:
			shallow = False

		args = [git, 'fetch']
		if 'tag' in source:
			if shallow:
				args.append('--depth=1')
			args.extend([source['git'], 'tag', source['tag']])
		else:
			# If a commit is specified, we need the branch's full history.
			# TODO: it's unclear whether this is the best strategy:
			#       - for simplicity, it might be easier to always pull the full history
			#       - some remotes support fetching individual SHA1s.
			if 'commit' in source or fixed_commit is not None:
				shallow = False
			# When initializing the repository, we fetch only one commit.
			# For updates, we fetch all *new* commits (= default behavior of 'git fetch').
			# We do not unshallow the repository.
			if init and shallow:
				args.append('--depth=1')
			args.extend([source['git'], 'refs/heads/' + source['branch']
					+ ':' + 'refs/remotes/origin/' + source['branch']])
		subprocess.check_call(args, cwd=src.source_dir)
	elif 'hg' in source:
		hg = shutil.which('hg')
		if hg is None:
			raise GenericException("mercurial (hg) not found; please install it and retry")
		_util.try_mkdir(src.source_dir)
		args = [hg, 'clone', source['hg'], src.source_dir]
		subprocess.check_call(args)
	elif 'svn' in source:
		svn = shutil.which('svn')
		if svn is None:
			raise GenericException("subversion (svn) not found; please install it and retry")
		_util.try_mkdir(src.source_dir)
		args = [svn, 'co', source['svn'], src.source_dir]
		subprocess.check_call(args)
	else:
		assert 'url' in source

		_util.try_mkdir(src.source_dir)
		with urllib.request.urlopen(source['url']) as req:
			with open(src.source_archive_file, 'wb') as f:
				shutil.copyfileobj(req, f)
