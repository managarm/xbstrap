# SPDX-License-Identifier: MIT

from enum import Enum
import os
import re
import shutil
import subprocess
import urllib.request

from . import util as _util

class RepoStatus(Enum):
	GOOD = 0
	MISSING = 1
	OUTDATED = 2

def vcs_name(src):
	if 'git' in src._this_yml:
		return 'git'
	elif 'hg' in src._this_yml:
		return 'hg'
	elif 'svn' in src._this_yml:
		return 'svn'
	elif 'url' in src._this_yml:
		return 'url'
	else:
		return None

def determine_git_version(git):
	output = subprocess.check_output([git, 'version'], encoding='ascii');
	matches = re.match(r'^git version (\d+).(\d+).(\d+)', output)
	if matches is None:
		raise RuntimeError(f"Could not parse git version string: '{output}'")
	return tuple(int(matches.group(i)) for i in range(1, 4))

def check_repo(src, subdir, *, check_remotes=0):
	if 'git' in src._this_yml:
		source_dir = os.path.join(subdir, src.name)

		xbstrap_mirror = src.cfg.xbstrap_mirror
		if xbstrap_mirror is None:
			git_url = src._this_yml['git']
		else:
			git_url = urllib.parse.urljoin(xbstrap_mirror + '/git/', src.name)

		def get_local_commit(ref):
			try:
				out = subprocess.check_output(['git', 'show-ref', '--verify', ref],
						cwd=source_dir, stderr=subprocess.DEVNULL).decode().splitlines()
			except subprocess.CalledProcessError:
				return None
			assert len(out) == 1
			(commit, outref) = out[0].split(' ')
			return commit

		def get_remote_commit(ref):
			try:
				out = subprocess.check_output(['git', 'ls-remote', '--exit-code',
					git_url, ref]).decode().splitlines()
			except subprocess.CalledProcessError:
				return None
			assert len(out) == 1
			(commit, outref) = out[0].split('\t')
			return commit

		# There is a TOCTOU here; we assume that users do not concurrently delete directories.
		if not os.path.isdir(source_dir):
			return RepoStatus.MISSING
		if 'tag' in src._this_yml:
			ref = 'refs/tags/' + src._this_yml['tag']
			tracking_ref = 'refs/tags/' + src._this_yml['tag']
		else:
			ref = 'refs/heads/' + src._this_yml['branch']
			tracking_ref = 'refs/remotes/origin/' + src._this_yml['branch']
		local_commit = get_local_commit(tracking_ref)
		if local_commit is None:
			return RepoStatus.MISSING

		# Only check remote commits for
		do_check_remote = False
		if check_remotes >= 2:
			do_check_remote = True
		if check_remotes >= 1 and 'tag' not in src._this_yml:
			do_check_remote = True

		if do_check_remote:
			_util.log_info('Checking for remote updates of {}'.format(src.name))
			remote_commit = get_remote_commit(ref)
			if local_commit != remote_commit:
				return RepoStatus.OUTDATED
	elif 'hg' in src._this_yml:
		source_dir = os.path.join(subdir, src.name)

		if not os.path.isdir(source_dir):
			return RepoStatus.MISSING
		args = ['hg', 'manifest', '--pager', 'never', '-r',]
		if 'tag' in src._this_yml:
			args.append(src._this_yml['tag'])
		else:
			args.append(src._this_yml['branch'])
		if subprocess.call(args, cwd=source_dir, stdout=subprocess.DEVNULL) != 0:
			return RepoStatus.MISSING
	elif 'svn' in src._this_yml:
		source_dir = os.path.join(subdir, src.name)

		if not os.path.isdir(source_dir):
			return RepoStatus.MISSING
	elif 'url' in src._this_yml:
		source_archive_file = os.path.join(subdir, src.name + '.' + src.source_archive_format)

		if not os.access(source_archive_file, os.F_OK):
			return RepoStatus.MISSING
	else:
		# VCS-less source.
		source_dir = os.path.join(subdir, src.name)

		if not os.path.isdir(source_dir):
			return RepoStatus.MISSING

	return RepoStatus.GOOD

def fetch_repo(cfg, src, subdir, *, ignore_mirror=False, bare_repo=False):
	source = src._this_yml

	if 'git' in source:
		source_dir = os.path.join(subdir, src.name)

		if ignore_mirror:
			xbstrap_mirror = None
		else:
			xbstrap_mirror = src.cfg.xbstrap_mirror
		if xbstrap_mirror is None:
			git_url = src._this_yml['git']
		else:
			git_url = urllib.parse.urljoin(xbstrap_mirror + '/git/', src.name)

		git = shutil.which('git')
		if git is None:
			raise GenericException("git not found; please install it and retry")
		commit_yml = cfg._commit_yml.get('commits', dict()).get(src.name, dict())
		fixed_commit = commit_yml.get('fixed_commit', None)

		git_version = determine_git_version(git)

		# Newer versions of git remit a warning if -b is not passed.
		# (We do not care about the name of the master branch, but we need to
		# get rid of the warning.)
		b_args = []
		if git_version >= (2, 28, 0):
			b_args = ['-b', 'master']

		init = not os.path.isdir(source_dir)
		if init:
			_util.try_mkdir(source_dir)
			if bare_repo:
				subprocess.check_call([git, 'init', '--bare'], cwd=source_dir)
			else:
				subprocess.check_call([git, 'init'] + b_args, cwd=source_dir)
			# We always set the remote to the true remote, not a mirror.
			subprocess.check_call([git, 'remote', 'add', 'origin', source['git']],
					cwd=source_dir)

		shallow = not source.get('disable_shallow_fetch', False)
		# We have to disable shallow fetches to get rolling versions right.
		if src.is_rolling_version:
			shallow = False

		# We cannot shallow clone mirrors
		if bare_repo:
			shallow = False

		args = [git, 'fetch']
		if 'tag' in source:
			if shallow:
				args.append('--depth=1')
			args.extend([git_url, 'tag', source['tag']])
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


			# For bare repos, we mirror the original repo
			# (in particular, we do not distinguish local and remote branches).
			if bare_repo:
				args.extend([git_url, 'refs/heads/' + source['branch']
						+ ':' + 'refs/heads/' + source['branch']])
			else:
				args.extend([git_url, 'refs/heads/' + source['branch']
						+ ':' + 'refs/remotes/origin/' + source['branch']])
		subprocess.check_call(args, cwd=source_dir)
	elif 'hg' in source:
		source_dir = os.path.join(subdir, src.name)

		hg = shutil.which('hg')
		if hg is None:
			raise GenericException("mercurial (hg) not found; please install it and retry")
		_util.try_mkdir(source_dir)
		args = [hg, 'clone', source['hg'], source_dir]
		subprocess.check_call(args)
	elif 'svn' in source:
		source_dir = os.path.join(subdir, src.name)

		svn = shutil.which('svn')
		if svn is None:
			raise GenericException("subversion (svn) not found; please install it and retry")
		_util.try_mkdir(source_dir)
		args = [svn, 'co', source['svn'], source_dir]
		subprocess.check_call(args)
	elif 'url' in source:
		source_dir = os.path.join(subdir, src.name)
		source_archive_file = os.path.join(subdir, src.name + '.' + src.source_archive_format)

		_util.try_mkdir(source_dir)
		with urllib.request.urlopen(source['url']) as req:
			with open(source_archive_file, 'wb') as f:
				shutil.copyfileobj(req, f)
	else:
		# VCS-less source.
		source_dir = os.path.join(subdir, src.name)
		_util.try_mkdir(source_dir)
