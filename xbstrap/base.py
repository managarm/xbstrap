# SPDX-License-Identifier: MIT

import collections
from enum import Enum
import errno
import filecmp
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import stat
import sys
import tarfile
import tempfile
import zipfile

import colorama
import jsonschema
import yaml

verbosity = False

global_yaml_loader = yaml.SafeLoader
global_bootstrap_validator = None
native_yaml_available = False

try:
	global_yaml_loader = yaml.CSafeLoader
	native_yaml_available = True
except AttributeError:
	pass

def load_bootstrap_yaml(path):
	global global_bootstrap_validator
	if not global_bootstrap_validator:
		schema_path = os.path.join(os.path.dirname(__file__), 'schema.yml')
		with open(schema_path, 'r') as f:
			schema_yml = yaml.load(f, Loader=global_yaml_loader)
		global_bootstrap_validator = jsonschema.Draft7Validator(schema_yml)

	with open(path, 'r') as f:
		yml = yaml.load(f, Loader=global_yaml_loader)

	any_errors = False
	n = 0
	for e in global_bootstrap_validator.iter_errors(yml):
		if n == 0:
			print("{}xbstrap{}: {}Failed to validate boostrap.yml{}".format(
					colorama.Style.BRIGHT, colorama.Style.NORMAL,
					colorama.Fore.RED, colorama.Style.RESET_ALL),
					file=sys.stderr)
		print("         {}* Error in file: {}, YAML element: {}\n"
				"           {}{}".format(colorama.Fore.RED,
				path, '/'.join(str(elem) for elem in e.absolute_path), e.message,
				colorama.Style.RESET_ALL),
				file=sys.stderr)
		any_errors = True
		n += 1
		if n >= 10:
			print("{}xbstrap{}: {}Reporting only the first 10 errors{}".format(
					colorama.Style.BRIGHT, colorama.Style.NORMAL,
					colorama.Fore.RED, colorama.Style.RESET_ALL),
					file=sys.stderr)
			break

	if any_errors:
		print("{}xbstrap{}: {}Validation issues will become hard errors in the future{}".format(
				colorama.Style.BRIGHT, colorama.Style.NORMAL, colorama.Fore.YELLOW,
				colorama.Style.RESET_ALL),
				file=sys.stderr)

	return yml

def touch(path):
	with open(path, 'w') as f:
		pass

def try_mkdir(path, recursive=False):
	try:
		if not recursive:
			os.mkdir(path)
		else:
			os.makedirs(path)
	except OSError as e:
		if e.errno != errno.EEXIST:
			raise

def try_unlink(path):
	try:
		os.unlink(path)
	except OSError as e:
		if e.errno != errno.ENOENT:
			raise

def try_rmtree(path):
	try:
		shutil.rmtree(path)
	except OSError as e:
		if e.errno != errno.ENOENT:
			raise

def num_allocated_cpus():
	try:
		cpuset = os.sched_getaffinity(0)
	except AttributeError:
		# MacOS does not have CPU affinity.
		return None
	return len(cpuset)

def get_concurrency():
	n = num_allocated_cpus()
	if n is None:
		# The best that we can do is returning the number of all CPUs.
		n = os.cpu_count()
	return n

def replace_at_vars(string, resolve):
	def do_substitute(m):
		varname = m.group(1)
		result = resolve(varname)
		if result is None:
			raise RuntimeError("Unexpected substitution {}".format(varname))
		return result

	return re.sub(r'@(\w+)@', do_substitute, string)

def installtree(src_root, dest_root):
	for name in os.listdir(src_root):
		src_path = os.path.join(src_root, name)
		dest_path = os.path.join(dest_root, name)

		# We do islink before isdir, as isdir resolves symlinks
		if os.path.islink(src_path):
			try_unlink(dest_path)
			# Do not preserve attributes
			os.symlink(os.readlink(src_path), dest_path)
		elif os.path.isdir(src_path):
			if not os.access(dest_path, os.F_OK):
				# We only copy attributes when the directory is first created.
				os.mkdir(dest_path)
				shutil.copystat(src_path, dest_path)

			installtree(src_path, dest_path)
		else:
			try_unlink(dest_path)
			shutil.copy2(src_path, dest_path)

class ResetMode(Enum):
	NONE = 0
	RESET = 1
	HARD_RESET = 1

class ItemSettings:
	def __init__(self):
		# 0 = Do not check for updates.
		# 1 = Check for updates.
		# 2 = Check even for "crazy" updates (e.g., modification of VCS tags).
		self.check_remotes = 0
		self.reset = ResetMode.NONE

class ItemState:
	__slots__ = ['missing', 'updatable', 'timestamp']

	def __init__(self, missing=False, updatable=False, timestamp=None):
		self.missing = missing
		self.updatable = updatable
		self.timestamp = timestamp

ArtifactFile = collections.namedtuple('ArtifactFile', ['name', 'filepath'])

class Config:
	def __init__(self, path):
		self._config_path = path
		self._root_yml = None
		self._site_yml = dict()
		self._sources = dict()
		self._tool_pkgs = dict()
		self._tool_stages = dict()
		self._target_pkgs = dict()
		self._tasks = dict()

		self._bootstrap_path = os.path.join(path,
				os.path.dirname(os.readlink(os.path.join(path, 'bootstrap.link'))))

		root_path = os.path.join(path, os.readlink(os.path.join(path, 'bootstrap.link')))
		self._root_yml = load_bootstrap_yaml(root_path)

		try:
			with open(os.path.join(path, 'bootstrap-site.yml'), 'r') as f:
				self._site_yml = yaml.load(f, Loader=global_yaml_loader)
		except FileNotFoundError:
			pass

		self._parse_yml(os.path.join(path, os.readlink(os.path.join(path, 'bootstrap.link'))),
				self._root_yml)

	def _parse_yml(self, current_path, current_yml,
			filter_sources = None, filter_tools = None, filter_pkgs = None, filter_tasks = None):
		if 'imports' in current_yml and isinstance(current_yml['imports'], list):
			if current_yml is not self._root_yml:
				raise RuntimeError("Nested imports are not supported")
			for import_def in current_yml['imports']:
				if 'from' not in import_def and 'file' not in import_def:
					raise RuntimeError("Unexpected data in import")
				elif 'from' in import_def and 'file' in import_def:
					raise RuntimeError("Unexpected data in import")

				if 'from' in import_def:
					import_path = os.path.join(os.path.dirname(current_path),
							str(import_def['from']))
					filter = dict()
					import_yml = load_bootstrap_yaml(import_path)
					for f in ['sources', 'tools', 'packages', 'tasks']:
						if "all_"+f in import_def:
							filter[f] = None
						elif f in import_def:
							filter[f] = import_def[f]
						else:
							filter[f] = []
					self._parse_yml(import_path, import_yml,
							filter_sources=filter['sources'], filter_tools=filter['tools'],
							filter_pkgs=filter['packages'], filter_tasks=filter['tasks'])
				elif 'file' in import_def:
					import_path = os.path.join(os.path.dirname(current_path),
							str(import_def['file']))
					import_yml = load_bootstrap_yaml(import_path)
					self._parse_yml(import_path, import_yml)

		if 'sources' in current_yml and isinstance(current_yml['sources'], list):
			for src_yml in current_yml['sources']:
				src = Source(self, None, src_yml)
				if not (filter_sources is None) and (src.name not in filter_sources):
					continue
				if src.name in self._sources:
					raise RuntimeError("Duplicate source {}".format(src.name))
				self._sources[src.name] = src

		if 'tools' in current_yml and isinstance(current_yml['tools'], list):
			for pkg_yml in current_yml['tools']:
				if 'source' in pkg_yml:
					src = Source(self, pkg_yml['name'], pkg_yml['source'])
					if src.name in self._sources:
						raise RuntimeError("Duplicate source {}".format(src.name))
					self._sources[src.name] = src
				pkg = HostPackage(self, pkg_yml)
				if not (filter_tools is None) and (pkg.name not in filter_tools):
					continue
				self._tool_pkgs[pkg.name] = pkg

		if 'packages' in current_yml and isinstance(current_yml['packages'], list):
			for pkg_yml in current_yml['packages']:
				if 'source' in pkg_yml:
					src = Source(self, pkg_yml['name'], pkg_yml['source'])
					if src.name in self._sources:
						raise RuntimeError("Duplicate source {}".format(src.name))
					self._sources[src.name] = src
				pkg = TargetPackage(self, pkg_yml)
				if not (filter_pkgs is None) and (pkg.name not in filter_pkgs):
					continue
				self._target_pkgs[pkg.name] = pkg

		if 'tasks' in current_yml and isinstance(current_yml['tasks'], list):
			for task_yml in current_yml['tasks']:
				if not 'name' in task_yml:
					raise RuntimeError("no name specified for task")
				if not (filter_tasks is None) and (task.name not in filter_tasks):
					continue
				task = RunTask(self, task_yml)
				self._tasks[task.name] = task

	@property
	def patch_author(self):
		default = 'xbstrap'
		if 'general' not in self._root_yml:
			return default
		return self._root_yml['general'].get('patch_author', default)

	@property
	def patch_email(self):
		default = 'xbstrap@localhost'
		if 'general' not in self._root_yml:
			return default
		return self._root_yml['general'].get('patch_email', default)

	@property
	def everything_by_default(self):
		if 'general' not in self._root_yml:
			return True
		return self._root_yml['general'].get('everything_by_default', True)

	@property
	def repository_url(self):
		if 'repository' not in self._root_yml:
			return None
		if 'url' not in self._root_yml['repository']:
			return None
		return self._root_yml['repository']['url']

	@property
	def use_xbps(self):
		if 'pkg_management' not in self._site_yml:
			return False
		if 'format' not in self._site_yml['pkg_management']:
			return False
		return self._site_yml['pkg_management']['format'] == 'xbps'

	@property
	def source_root(self):
		return os.path.join(os.getcwd(),
				os.path.dirname(os.readlink('bootstrap.link')))

	@property
	def build_root(self):
		return os.getcwd()

	@property
	def sysroot_dir(self):
		if 'directories' not in self._root_yml or 'system_root' not in self._root_yml['directories']:
			return os.path.join(self.build_root, 'system-root')
		else:
			return os.path.join(self.build_root, self._root_yml['directories']['system_root'])

	@property
	def xbps_repository_dir(self):
		return os.path.join(self.build_root, 'xbps-repo')

	@property
	def tool_build_dir(self):
		if 'directories' not in self._root_yml or 'pkg_builds' not in self._root_yml['directories']:
			return os.path.join(self.build_root, 'tool-builds')
		else:
			return os.path.join(self.build_root, self._root_yml['directories']['tool_builds'])

	@property
	def pkg_build_dir(self):
		if 'directories' not in self._root_yml or 'pkg_builds' not in self._root_yml['directories']:
			return os.path.join(self.build_root, 'pkg-builds')
		else:
			return os.path.join(self.build_root, self._root_yml['directories']['pkg_builds'])

	@property
	def tool_out_dir(self):
		if 'directories' not in self._root_yml or 'tools' not in self._root_yml['directories']:
			return os.path.join(self.build_root, 'tools')
		else:
			return os.path.join(self.build_root, self._root_yml['directories']['tools'])

	@property
	def package_out_dir(self):
		if 'directories' not in self._root_yml or 'packages' not in self._root_yml['directories']:
			return os.path.join(self.build_root, 'packages')
		else:
			return os.path.join(self.build_root, self._root_yml['directories']['packages'])

	def get_source(self, name):
		return self._sources[name]

	def get_tool_pkg(self, name):
		return self._tool_pkgs[name]

	def get_task(self, name):
		if name in self._tasks:
			return self._tasks[name]

	def all_tools(self):
		yield from self._tool_pkgs.values()

	def all_pkgs(self):
		yield from self._target_pkgs.values()

	def get_target_pkg(self, name):
		return self._target_pkgs[name]

class ScriptStep:
	def __init__(self, step_yml):
		self._step_yml = step_yml

	@property
	def args(self):
		return self._step_yml['args']

	@property
	def environ(self):
		if 'environ' not in self._step_yml:
			return dict()
		return self._step_yml['environ']

	@property
	def workdir(self):
		if 'workdir' not in self._step_yml:
			return None
		return self._step_yml['workdir']

	@property
	def quiet(self):
		if 'quiet' not in self._step_yml:
			return False
		return self._step_yml['quiet']

class RequirementsMixin:
	@property
	def source_dependencies(self):
		if 'sources_required' in self._this_yml:
			yield from self._this_yml['sources_required']

	@property
	def tool_dependencies(self):
		deps = set(tool_name for tool_name, stage_name in self.tool_stage_dependencies)
		yield from deps

	@property
	def tool_stage_dependencies(self):
		tools_seen = set()
		tools_stack = [ ]

		def visit(stage):
			if stage.subject_id in tools_seen:
				return
			tools_seen.add(stage.subject_id)
			tools_stack.append(stage)

		def visit_yml(yml):
			if isinstance(yml, dict):
				if 'virtual' in yml:
					return
				if 'stage_dependencies' in yml:
					for stage_name in yml['stage_dependencies']:
						visit(self._cfg.get_tool_pkg(yml['tool']).get_stage(stage_name))
				else:
					for stage in self._cfg.get_tool_pkg(yml['tool']).all_stages():
						visit(stage)
			else:
				assert isinstance(yml, str)
				for stage in self._cfg.get_tool_pkg(yml).all_stages():
					visit(stage)

		for yml in self._this_yml.get('tools_required', []):
			visit_yml(yml)

		while tools_stack:
			stage = tools_stack.pop()
			yield stage.subject_id

			for yml in stage.pkg._this_yml.get('tools_required', []):
				if not isinstance(yml, dict):
					continue
				if not yml.get('recursive', False):
					continue
				visit_yml(yml)

	@property
	def virtual_tools(self):
		if 'tools_required' in self._this_yml:
			for yml in self._this_yml['tools_required']:
				if not isinstance(yml, dict):
					continue
				if 'virtual' in yml:
					yield yml

	@property
	def pkg_dependencies(self):
		if 'pkgs_required' in self._this_yml:
			yield from self._this_yml['pkgs_required']

	@property
	def task_dependencies(self):
		if 'tasks_required' in self._this_yml:
			for yml in self._this_yml['tasks_required']:
				if isinstance(yml, dict):
					if 'order_only' not in yml or not yml['order_only']:
						yield yml['task']
				else:
					assert isinstance(yml, str)
					yield yml

	@property
	def tasks_ordered_before(self):
		if 'tasks_required' in self._this_yml:
			for yml in self._this_yml['tasks_required']:
				if isinstance(yml, dict):
					if 'order_only' in yml and yml['order_only']:
						yield yml['task']

	def discover_recursive_pkg_dependencies(self):
		s = set()
		stack = [self]
		while stack:
			subject = stack.pop()

			for dep_name in subject.pkg_dependencies:
				dep = self._cfg.get_target_pkg(dep_name)
				if dep_name not in s:
					stack.append(dep)
				s.add(dep_name)
		return s

	def xbps_dependency_string(self):
		deps = ''
		for dep in self.pkg_dependencies:
			deps += ' {}>=0'.format(dep)
		return deps[1:]

class Source(RequirementsMixin):
	def __init__(self, cfg, induced_name, yml):
		self._cfg = cfg
		self._name = None
		self._this_yml = yml
		self._regenerate_steps = [ ]

		if 'name' in self._this_yml:
			self._name = self._this_yml['name']
		else:
			self._name = induced_name

		if 'regenerate' in self._this_yml:
			for step_yml in self._this_yml['regenerate']:
				self._regenerate_steps.append(ScriptStep(step_yml))

	@property
	def name(self):
		return self._name

	@property
	def subject_id(self):
		return self._name

	@property
	def subject_type(self):
		return 'source'

	@property
	def has_explicit_version(self):
		return 'version' in self._this_yml

	@property
	def version(self):
		return self._this_yml.get('version', '0.0')

	@property
	def sub_dir(self):
		if 'subdir' in self._this_yml:
			return os.path.join(self._cfg.source_root, self._this_yml['subdir'])
		return self._cfg.source_root

	@property
	def source_dir(self):
		return os.path.join(self.sub_dir, self._name)

	@property
	def source_archive_format(self):
		assert 'url' in self._this_yml
		return self._this_yml['format']

	@property
	def source_archive_file(self):
		assert 'url' in self._this_yml
		return os.path.join(self.sub_dir, self._name + '.' + self.source_archive_format)

	@property
	def patch_dir(self):
		return os.path.join(self._cfg.source_root, 'patches', self._name)

	@property
	def regenerate_steps(self):
		yield from self._regenerate_steps

	def check_if_fetched(self, settings):
		if 'git' in self._this_yml:
			def get_local_commit(ref):
				try:
					out = subprocess.check_output(['git', 'show-ref', '--verify', ref],
							cwd=self.source_dir, stderr=subprocess.DEVNULL).decode().splitlines()
				except subprocess.CalledProcessError:
					return None
				assert len(out) == 1
				(commit, outref) = out[0].split(' ')
				return commit

			def get_remote_commit(ref):
				try:
					out = subprocess.check_output(['git', 'ls-remote', '--exit-code',
						self._this_yml['git'], ref]).decode().splitlines()
				except subprocess.CalledProcessError:
					return None
				assert len(out) == 1
				(commit, outref) = out[0].split('\t')
				return commit

			# There is a TOCTOU here; we assume that users do not concurrently delete directories.
			if not os.path.isdir(self.source_dir):
				return ItemState(missing=True)
			if 'tag' in self._this_yml:
				ref = 'refs/tags/' + self._this_yml['tag']
				tracking_ref = 'refs/tags/' + self._this_yml['tag']
			else:
				ref = 'refs/heads/' + self._this_yml['branch']
				tracking_ref = 'refs/remotes/origin/' + self._this_yml['branch']
			local_commit = get_local_commit(tracking_ref)
			if local_commit is None:
				return ItemState(missing=True)

			# Only check remote commits for
			check_remote = False
			if settings.check_remotes >= 2:
				check_remote = True
			if settings.check_remotes >= 1 and 'tag' not in self._this_yml:
				check_remote = True

			if check_remote:
				print('{}xbstrap{}: Checking for remote updates of {}'.format(
						colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
						self.name))
				remote_commit = get_remote_commit(ref)
				if local_commit != remote_commit:
					return ItemState(updatable=True)
		elif 'hg' in self._this_yml:
			if not os.path.isdir(self.source_dir):
				return ItemState(missing=True)
			args = ['hg', 'manifest', '--pager', 'never', '-r',]
			if 'tag' in self._this_yml:
				args.append(self._this_yml['tag'])
			else:
				args.append(self._this_yml['branch'])
			if subprocess.call(args, cwd=self.source_dir, stdout=subprocess.DEVNULL) != 0:
				return ItemState(missing=True)
		elif 'svn' in self._this_yml:
			if not os.path.isdir(self.source_dir):
				return ItemState(missing=True)
		else:
			assert 'url' in self._this_yml
			if not os.access(self.source_archive_file, os.F_OK):
				return ItemState(missing=True)

		path = os.path.join(self.source_dir, 'fetched.xbstrap')
		try:
			stat = os.stat(path)
		except FileNotFoundError:
			# This is a special case: we already found that the commit exists.
			return ItemState()
		return ItemState(timestamp=stat.st_mtime)

	def mark_as_fetched(self):
		touch(os.path.join(self.source_dir, 'fetched.xbstrap'))

	def check_if_checkedout(self, settings):
		path = os.path.join(self.source_dir, 'checkedout.xbstrap')
		try:
			stat = os.stat(path)
		except FileNotFoundError:
			return ItemState(missing=True)
		return ItemState(timestamp=stat.st_mtime)

	def mark_as_checkedout(self):
		touch(os.path.join(self.source_dir, 'checkedout.xbstrap'))

	def check_if_patched(self, settings):
		path = os.path.join(self.source_dir, 'patched.xbstrap')
		try:
			stat = os.stat(path)
		except FileNotFoundError:
			return ItemState(missing=True)
		return ItemState(timestamp=stat.st_mtime)

	def mark_as_patched(self):
		touch(os.path.join(self.source_dir, 'patched.xbstrap'))

	def check_if_regenerated(self, settings):
		path = os.path.join(self.source_dir, 'regenerated.xbstrap')
		try:
			stat = os.stat(path)
		except FileNotFoundError:
			return ItemState(missing=True)
		return ItemState(timestamp=stat.st_mtime)

	def mark_as_regenerated(self):
		touch(os.path.join(self.source_dir, 'regenerated.xbstrap'))

class HostStage(RequirementsMixin):
	def __init__(self, cfg, pkg, inherited, stage_yml):
		self._cfg = cfg
		self._pkg = pkg
		self._inherited = inherited
		self._this_yml = stage_yml
		self._compile_steps = [ ]
		self._install_steps = [ ]

		if 'compile' in self._this_yml:
			for step_yml in self._this_yml['compile']:
				self._compile_steps.append(ScriptStep(step_yml))
		if 'install' in self._this_yml:
			for step_yml in self._this_yml['install']:
				self._install_steps.append(ScriptStep(step_yml))

	@property
	def pkg(self):
		return self._pkg

	@property
	def stage_name(self):
		if self._inherited:
			return None
		return self._this_yml['name']

	@property
	def subject_id(self):
		return (self._pkg.name, self.stage_name)

	@property
	def subject_type(self):
		return 'tool stage'

	@property
	def compile_steps(self):
		yield from self._compile_steps

	@property
	def install_steps(self):
		yield from self._install_steps

	def check_if_compiled(self, settings):
		stage_spec = ''
		if not self._inherited:
			stage_spec = '@' + self.stage_name
		path = os.path.join(self._pkg.build_dir, 'built' + stage_spec + '.xbstrap')
		try:
			stat = os.stat(path)
		except FileNotFoundError:
			return ItemState(missing=True)
		return ItemState(timestamp=stat.st_mtime)

	def mark_as_compiled(self):
		stage_spec = ''
		if not self._inherited:
			stage_spec = '@' + self.stage_name
		touch(os.path.join(self._pkg.build_dir, 'built' + stage_spec + '.xbstrap'))

	def check_if_installed(self, settings):
		stage_spec = ''
		if not self._inherited:
			stage_spec = '@' + self.stage_name
		path = os.path.join(self._pkg.prefix_dir, 'etc', 'xbstrap',
				self._pkg.name + stage_spec + '.installed')
		try:
			stat = os.stat(path)
		except FileNotFoundError:
			return ItemState(missing=True)
		return ItemState(timestamp=stat.st_mtime)

	def mark_as_installed(self):
		stage_spec = ''
		if not self._inherited:
			stage_spec = '@' + self.stage_name
		try_mkdir(os.path.join(self._pkg.prefix_dir, 'etc'))
		try_mkdir(os.path.join(self._pkg.prefix_dir, 'etc', 'xbstrap'))
		path = os.path.join(self._pkg.prefix_dir, 'etc', 'xbstrap',
				self._pkg.name + stage_spec + '.installed')
		touch(path)


class HostPackage(RequirementsMixin):
	def __init__(self, cfg, pkg_yml):
		self._cfg = cfg
		self._this_yml = pkg_yml
		self._configure_steps = [ ]
		self._stages = dict()
		self._tasks = dict()

		if 'stages' in self._this_yml:
			for stage_yml in self._this_yml['stages']:
				stage = HostStage(self._cfg, self, False, stage_yml)
				self._stages[stage.stage_name] = stage
		else:
			stage = HostStage(self._cfg, self, True, self._this_yml)
			self._stages[stage.stage_name] = stage

		if 'configure' in self._this_yml:
			for step_yml in self._this_yml['configure']:
				self._configure_steps.append(ScriptStep(step_yml))

		if 'tasks' in self._this_yml:
			for task_yml in self._this_yml['tasks']:
				if not 'name' in task_yml:
					raise RuntimeError("no name specified in task of tool {}".format(self.name))
				self._tasks[task_yml['name']] = PackageRunTask(cfg, self, task_yml)

	@property
	def exports_shared_libs(self):
		if 'exports_shared_libs' not in self._this_yml:
			return False
		return self._this_yml['exports_shared_libs']

	@property
	def exports_aclocal(self):
		if 'exports_aclocal' not in self._this_yml:
			return False
		return self._this_yml['exports_aclocal']

	@property
	def source(self):
		if 'from_source' in self._this_yml:
			return self._this_yml['from_source']
		if 'name' in self._this_yml['source']:
			return self._this_yml['source']['name']
		return self.name

	@property
	def recursive_tools_required(self):
		if 'tools_required' in self._this_yml:
			for yml in self._this_yml['tools_required']:
				if not isinstance(yml, dict):
					continue
				if 'virtual' in yml:
					continue
				if 'recursive' not in yml:
					continue
				yield yml['tool']

	@property
	def build_dir(self):
		return os.path.join(self._cfg.tool_build_dir, self.name)

	@property
	def prefix_dir(self):
		return os.path.join(self._cfg.tool_out_dir, self.name)

	@property
	def archive_file(self):
		return os.path.join(self._cfg.tool_out_dir, self.name + '.tar.gz')

	@property
	def name(self):
		return self._this_yml['name']

	@property
	def subject_id(self):
		return self.name

	@property
	def subject_type(self):
		return 'tool'

	@property
	def is_default(self):
		if 'default' not in self._this_yml:
			return self._cfg.everything_by_default
		return self._this_yml['default']

	@property
	def stability_level(self):
		return self._this_yml.get('stability_level', 'stable')

	def all_stages(self):
		yield from self._stages.values()

	def get_stage(self, name):
		return self._stages[name]

	def get_task(self, task):
		if task in self._tasks:
			return self._tasks[task]
		return False

	@property
	def configure_steps(self):
		yield from self._configure_steps

	@property
	def version(self):
		source = self._cfg.get_source(self.source)

		# If no version is specified, we fall back to 0.0_0.
		if not source.has_explicit_version and 'revision' not in self._this_yml:
			return source.version + '_0'

		revision = self._this_yml.get('revision', 1)
		if revision < 1:
			raise RuntimeError("Tool {} specifies a revision < 1".format(self.name));

		return source.version + '_' + str(revision)

	def check_if_configured(self, settings):
		path = os.path.join(self.build_dir, 'configured.xbstrap')
		try:
			stat = os.stat(path)
		except FileNotFoundError:
			return ItemState(missing=True)
		return ItemState(timestamp=stat.st_mtime)

	def mark_as_configured(self, mark=True):
		if mark:
			touch(os.path.join(self.build_dir, 'configured.xbstrap'))
		else:
			os.unlink(os.path.join(self.build_dir, 'configured.xbstrap'))

	def check_if_fully_installed(self, settings):
		for stage in self.all_stages():
			state = stage.check_if_installed(settings)
			if state.missing:
				return ItemState(missing=True)
		return ItemState()

	def check_if_archived(self, settings):
		try:
			stat = os.stat(self.archive_file)
		except FileNotFoundError:
			return ItemState(missing=True)
		return ItemState(timestamp=stat.st_mtime)

class TargetPackage(RequirementsMixin):
	def __init__(self, cfg, pkg_yml):
		self._cfg = cfg
		self._this_yml = pkg_yml
		self._configure_steps = [ ]
		self._build_steps = [ ]
		self._tasks = dict()

		if 'configure' in self._this_yml:
			for step_yml in self._this_yml['configure']:
				self._configure_steps.append(ScriptStep(step_yml))

		if 'build' in self._this_yml:
			for step_yml in self._this_yml['build']:
				self._build_steps.append(ScriptStep(step_yml))

		if 'tasks' in self._this_yml:
			for task_yml in self._this_yml['tasks']:
				if not 'name' in task_yml:
					raise RuntimeError("no name specified in task of package {}".format(self.name))
				self._tasks[task_yml['name']] = PackageRunTask(cfg, self, task_yml)

	@property
	def source(self):
		if 'from_source' in self._this_yml:
			return self._this_yml['from_source']
		if 'name' in self._this_yml['source']:
			return self._this_yml['source']['name']
		return self.name

	@property
	def build_dir(self):
		return os.path.join(self._cfg.pkg_build_dir, self.name)

	@property
	def staging_dir(self):
		return os.path.join(self._cfg.package_out_dir, self.name)

	@property
	def collect_dir(self):
		return os.path.join(self._cfg.package_out_dir, self.name + '.collect')

	@property
	def archive_file(self):
		return os.path.join(self._cfg.package_out_dir, self.name + '.tar.gz')

	@property
	def name(self):
		return self._this_yml['name']

	@property
	def subject_id(self):
		return self.name

	@property
	def subject_type(self):
		return 'package'

	@property
	def is_default(self):
		if 'default' not in self._this_yml:
			return self._cfg.everything_by_default
		return self._this_yml['default']

	@property
	def stability_level(self):
		return self._this_yml.get('stability_level', 'stable')

	@property
	def is_implicit(self):
		if 'implict_package' not in self._this_yml:
			return False
		return self._this_yml['implict_package']

	@property
	def configure_steps(self):
		yield from self._configure_steps

	@property
	def build_steps(self):
		yield from self._build_steps

	@property
	def version(self):
		source = self._cfg.get_source(self.source)

		# If no version is specified, we fall back to 0.0_0.
		if not source.has_explicit_version and 'revision' not in self._this_yml:
			return source.version + '_0'

		revision = self._this_yml.get('revision', 1)
		if revision < 1:
			raise RuntimeError("Package {} specifies a revision < 1".format(self.name));

		return source.version + '_' + str(revision)

	def get_task(self, task):
		if task in self._tasks:
			return self._tasks[task]
		return False

	def check_if_configured(self, settings):
		path = os.path.join(self.build_dir, 'configured.xbstrap')
		try:
			stat = os.stat(path)
		except FileNotFoundError:
			return ItemState(missing=True)
		return ItemState(timestamp=stat.st_mtime)

	def mark_as_configured(self, mark=True):
		if mark:
			touch(os.path.join(self.build_dir, 'configured.xbstrap'))
		else:
			os.unlink(os.path.join(self.build_dir, 'configured.xbstrap'))

	def check_staging(self, settings):
		if not os.access(self.staging_dir, os.F_OK):
			return ItemState(missing=True)
		return ItemState()

	def check_if_packed(self, settings):
		if self._cfg.use_xbps:
			try:
				out = subprocess.check_output(['xbps-query',
					'--repository=' + self._cfg.xbps_repository_dir,
					self.name
				])
				return ItemState()
			except subprocess.CalledProcessError:
				return ItemState(missing=True)
		else:
			raise RuntimeError('Package management configuration does not support pack')

	def check_if_installed(self, settings):
		if self._cfg.use_xbps:
			try:
				out = subprocess.check_output(['xbps-query',
					'-r', self._cfg.sysroot_dir,
					self.name
				])
				if b'state: installed' not in out.splitlines():
					return ItemState(missing=True)
				return ItemState()
			except subprocess.CalledProcessError:
				return ItemState(missing=True)
		else:
			path = os.path.join(self._cfg.sysroot_dir, 'etc', 'xbstrap', self.name + '.installed')
			if not os.access(path, os.F_OK):
				return ItemState(missing=True)
			return ItemState()

	def mark_as_installed(self):
		try_mkdir(os.path.join(self._cfg.sysroot_dir, 'etc'))
		try_mkdir(os.path.join(self._cfg.sysroot_dir, 'etc', 'xbstrap'))
		path = os.path.join(self._cfg.sysroot_dir, 'etc', 'xbstrap', self.name + '.installed')
		touch(path)

class PackageRunTask(RequirementsMixin):
	def __init__(self, cfg, pkg, task_yml):
		self._cfg = cfg
		self._pkg = pkg
		self._task = task_yml['name']
		self._script_step = ScriptStep(task_yml)

	@property
	def name(self):
		return "{}:{}".format(self._pkg.name, self._task)

	@property
	def task_name(self):
		return self._task

	@property
	def pkg(self):
		return self._pkg

	@property
	def script_step(self):
		return self._script_step

	@property
	def subject_id(self):
		return self.name

	@property
	def is_implicit(self):
		return False

	@property
	def subject_type(self):
		return 'task'

	@property
	def _this_yml(self):
		return self._pkg._this_yml

	@property
	def source(self):
		return self._pkg.source

class RunTask(RequirementsMixin):
	def __init__(self, cfg, task_yml):
		self._cfg = cfg
		self._this_yml = task_yml
		self._task = task_yml['name']
		self._script_step = ScriptStep(task_yml)

	@property
	def name(self):
		return self._task

	@property
	def script_step(self):
		return self._script_step

	@property
	def subject_id(self):
		return self.name

	@property
	def is_implicit(self):
		return False

	@property
	def subject_type(self):
		return 'task'

	@property
	def artifact_files(self):
		def substitute(varname):
			if varname == 'SOURCE_ROOT':
				return self._cfg.source_root
			elif varname == 'BUILD_ROOT':
				return self._cfg.build_root
			elif varname == 'SYSROOT_DIR':
				return self._cfg.sysroot_dir

		entries = self._this_yml.get('artifact_files', [])
		for e in entries:
			path = replace_at_vars(e['path'], substitute)
			yield ArtifactFile(e['name'], os.path.join(path, e['name']))

def config_for_dir():
	return Config('')

class EnvironmentComposer:
	def __init__(self, cfg):
		self.cfg = cfg
		self.path_dirs = [ ]
		self.shared_lib_dirs = [ ]
		self.aclocal_dirs = [ ]

	def compose(self, for_package=False, use_target_pkgconfig=False):
		environ = os.environ.copy()

		environ['XBSTRAP_SOURCE_ROOT'] = self.cfg.source_root
		environ['XBSTRAP_BUILD_ROOT'] = self.cfg.build_root
		environ['XBSTRAP_SYSROOT_DIR'] = self.cfg.sysroot_dir

		if for_package and not use_target_pkgconfig:
			pkgcfg_libdir = os.path.join(self.cfg.sysroot_dir, 'usr', 'lib', 'pkgconfig')
			pkgcfg_libdir += ':' + os.path.join(self.cfg.sysroot_dir, 'usr', 'share', 'pkgconfig')

			environ.pop('PKG_CONFIG_PATH', None)
			environ['PKG_CONFIG_SYSROOT_DIR'] = self.cfg.sysroot_dir
			environ['PKG_CONFIG_LIBDIR'] = pkgcfg_libdir

		self._prepend_dirs(environ, 'PATH', self.path_dirs)
		self._prepend_dirs(environ, 'LD_LIBRARY_PATH', self.shared_lib_dirs)
		self._prepend_dirs(environ, 'ACLOCAL_PATH', self.aclocal_dirs)
		return environ

	def _prepend_dirs(self, environ, varname, dirs):
		if not dirs:
			return
		joined = ':'.join(dirs)
		if varname in environ and environ[varname]:
			environ[varname] = joined + ':' + environ[varname]
		else:
			environ[varname] = joined

	def _append_dirs(self, environ, varname, dirs):
		if not dirs:
			return
		joined = ':'.join(dirs)
		if varname in environ and environ[varname]:
			environ[varname] += ':' + joined
		else:
			environ[varname] = joined

def run_tool(cfg, args, tool_pkgs=[], virtual_tools=[], workdir=None, extra_environ=dict(),
		for_package=False, quiet=False):
	pkg_queue = []
	pkg_visited = set()

	# /bin directory for virtual tools.
	use_target_pkgconfig = False
	vb = tempfile.TemporaryDirectory()

	for yml in virtual_tools:
		if yml['virtual'] == 'pkgconfig-for-target':
			vscript = os.path.join(vb.name, '{}-pkg-config'.format(yml['triple']))
			with open(vscript, 'wt') as f:
				f.write('#!/bin/sh\n'
					+ 'PKG_CONFIG_PATH= '
					+ ' PKG_CONFIG_SYSROOT_DIR="${XBSTRAP_SYSROOT_DIR}"'
					+ ' PKG_CONFIG_LIBDIR="${XBSTRAP_SYSROOT_DIR}/usr/lib/pkgconfig'
					+ ':${XBSTRAP_SYSROOT_DIR}/usr/share/pkgconfig"'
					+ ' pkg-config $@\n')
			os.chmod(vscript, 0o775)
			use_targe_pkgconfig = True
		else:
			raise RuntimeError("Unknown virtual tool {}".format(yml['virtual']))

	for pkg in tool_pkgs:
		assert pkg.name not in pkg_visited
		pkg_queue.append(pkg)
		pkg_visited.add(pkg.name)

	i = 0 # Need index-based loop as pkg_queue is mutated in the loop.
	while i < len(pkg_queue):
		pkg = pkg_queue[i]
		for dep_name in pkg.recursive_tools_required:
			if dep_name in pkg_visited:
				continue
			dep_pkg = cfg.get_tool_pkg(dep_name)
			pkg_queue.append(dep_pkg)
			pkg_visited.add(dep_name)
		i += 1

	composer = EnvironmentComposer(cfg)
	composer.path_dirs.append(vb.name)
	for pkg in pkg_queue:
		composer.path_dirs.append(os.path.join(pkg.prefix_dir, 'bin'))
		if pkg.exports_shared_libs:
			composer.shared_lib_dirs.append(os.path.join(pkg.prefix_dir, 'lib'))
		if pkg.exports_aclocal:
			composer.aclocal_dirs.append(os.path.join(pkg.prefix_dir, 'share/aclocal'))
	environ = composer.compose(for_package=for_package, use_target_pkgconfig=use_target_pkgconfig)
	environ.update(extra_environ)

	output = None # Default: Do not redirect output.
	if quiet:
		output = subprocess.DEVNULL

	print("{}xbstrap{}: Running {} (tools: {})".format(
			colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
			args, [tool.name for tool in pkg_queue]))
	subprocess.check_call(args, env=environ, cwd=workdir,
			stdout=output, stderr=output)

def run_step(cfg, step, default_workdir, substitute, tool_pkgs, virtual_tools,
		for_package=False):
	if isinstance(step.args, list):
		args = [replace_at_vars(arg, substitute) for arg in step.args]
	else:
		assert isinstance(step.args, str)
		args = ['/bin/bash', '-c', replace_at_vars(step.args, substitute)]

	environ = dict()
	for (key, value) in step.environ.items():
		environ[key] = replace_at_vars(value, substitute)

	if step.workdir is not None:
		workdir = replace_at_vars(step.workdir, substitute)
	else:
		workdir = default_workdir

	quiet = step.quiet and not verbosity

	run_tool(cfg, args, tool_pkgs=tool_pkgs, virtual_tools=virtual_tools, workdir=workdir,
			extra_environ=environ, for_package=for_package,
			quiet=quiet)

def postprocess_libtool(cfg, pkg):
	for libdir in ['lib', 'lib64', 'lib32', 'usr/lib', 'usr/lib64', 'usr/lib32']:
		filelist = []
		try:
			filelist = os.listdir(os.path.join(pkg.collect_dir, libdir))
		except OSError as e:
			if e.errno != errno.ENOENT:
				raise

		for ent in filelist:
			if not ent.endswith('.la'):
				continue
			print('xbstrap: Removing libtool file {}'.format(ent))
			os.unlink(os.path.join(pkg.collect_dir, libdir, ent))

# ---------------------------------------------------------------------------------------
# Source management.
# ---------------------------------------------------------------------------------------

def fetch_src(cfg, src):
	try_mkdir(src.sub_dir)
	source = src._this_yml

	if 'git' in source:
		init = not os.path.isdir(src.source_dir)
		if init:
			try_mkdir(src.source_dir)
			subprocess.check_call(['git', 'init'], cwd=src.source_dir)
			subprocess.check_call(['git', 'remote', 'add', 'origin', source['git']],
					cwd=src.source_dir)

		shallow = not source.get('disable_shallow_fetch', False)
		args = ['git', 'fetch']
		if 'tag' in source:
			if shallow:
				args.append('--depth=1')
			args.extend([source['git'], 'tag', source['tag']])
		else:
			# If a commit is specified, we need the branch's full history.
			# TODO: it's unclear whether this is the best strategy:
			#       - for simplicity, it might be easier to always pull the full history
			#       - some remotes support fetching individual SHA1s.
			if 'commit' in source:
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
		try_mkdir(src.source_dir)
		args = ['hg', 'clone', source['hg'], src.source_dir]
		subprocess.check_call(args)
	elif 'svn' in source:
		try_mkdir(src.source_dir)
		args = ['svn', 'co', source['svn'], src.source_dir]
		print (args)
		subprocess.check_call(args)
	else:
		assert 'url' in source

		try_mkdir(src.source_dir)
		with urllib.request.urlopen(source['url']) as req:
			with open(src.source_archive_file, 'wb') as f:
				shutil.copyfileobj(req, f)

	src.mark_as_fetched()

def checkout_src(cfg, src, settings):
	source = src._this_yml

	if 'git' in source:
		init = subprocess.call(['git', 'show-ref', '--verify', '-q', 'HEAD'],
				cwd=src.source_dir) != 0

		if 'tag' in source:
			if not init and settings.reset == ResetMode.HARD_RESET:
				subprocess.check_call(['git', 'reset', '--hard'], cwd=src.source_dir)
			if init or settings.reset != ResetMode.NONE:
				subprocess.check_call(['git', 'checkout', '--detach',
						'refs/tags/' + source['tag']], cwd=src.source_dir)
			else:
				raise RuntimeError("Refusing to checkout tag '{}' of source {}".format(
						source['tag'], src.name));
		else:
			commit = 'origin/' + source['branch']
			if 'commit' in source:
				commit = source['commit']
			if init or settings.reset != ResetMode.NONE:
				subprocess.check_call(['git', 'checkout', '--no-track',
						'-B', source['branch'], commit], cwd=src.source_dir)
				subprocess.call(['git', 'branch',
						'-u', 'refs/remotes/origin/' + source['branch']], cwd=src.source_dir)
			else:
				subprocess.check_call(['git', 'rebase', commit], cwd=src.source_dir)
	elif 'hg' in source:
		args = ['hg', 'checkout']
		if 'tag' in source:
			args.append(source['tag'])
		else:
			args.append(source['branch'])
		subprocess.check_call(args, cwd=src.source_dir)
	elif 'svn' in source:
		args = ['svn', 'update']
		if 'rev' in source:
			args.append('-r')
			args.append(source['rev'])
		subprocess.check_call(args, cwd=src.source_dir)
	else:
		if src.source_archive_format == 'raw' and 'filename' in source:
			shutil.copyfile(src.source_archive_file, os.path.join(src.sub_dir, src.name, source['filename']))
		elif src.source_archive_format.startswith('zip'):
			with zipfile.ZipFile(src.source_archive_file, 'r') as zip_ref:
				zip_ref.extractall(src.sub_dir)
		else:
			assert src.source_archive_format.startswith('tar.')

			compression = {
				'tar.gz': 'gz',
				'tar.xz': 'xz',
				'tar.bz2': 'bz2'
			}
			with tarfile.open(src.source_archive_file,
					'r|' + compression[src.source_archive_format]) as tar:
				for info in tar:
					if 'extract_path' not in source:
						prefix = ''
					else:
						prefix = source['extract_path'] + '/'
					if info.name.startswith(prefix):
						info.name = src.name + '/' + info.name[len(prefix):]
						tar.extract(info, src.sub_dir)

	src.mark_as_checkedout()

def patch_src(cfg, src):
	source = src._this_yml

	# Patches need to be applied in a sorted order.
	patches = []
	try:
		patches = os.listdir(src.patch_dir)
	except OSError as e:
		if e.errno != errno.ENOENT:
			raise
	patches.sort()

	for patch in patches:
		if not patch.endswith('.patch'):
			continue
		if 'git' in source:
			environ = os.environ.copy()
			environ['GIT_COMMITTER_NAME'] = cfg.patch_author
			environ['GIT_COMMITTER_EMAIL'] = cfg.patch_email
			subprocess.check_call(['git', 'am', '--committer-date-is-author-date',
					os.path.join(src.patch_dir, patch)],
				env=environ, cwd=src.source_dir)
		elif 'hg' in source:
			subprocess.check_call(['hg', 'import', os.path.join(src.patch_dir, patch)],
				cwd=src.source_dir)
		else:
			path_strip = str(source['patch-path-strip']) if 'patch-path-strip' in source else '0'
			with open(os.path.join(src.patch_dir, patch), 'r') as fd:
				subprocess.check_call(['patch', '-p', path_strip, '--merge'], stdin=fd, cwd=src.source_dir)

	src.mark_as_patched()

def regenerate_src(cfg, src):
	for step in src.regenerate_steps:
		tool_pkgs = []
		for dep_name in src.tool_dependencies:
			tool_pkgs.append(cfg.get_tool_pkg(dep_name))

		def substitute(varname):
			if varname == 'SOURCE_ROOT':
				return cfg.source_root
			elif varname == 'BUILD_ROOT':
				return cfg.build_root
			elif varname == 'SYSROOT_DIR':
				return cfg.sysroot_dir
			if varname == 'THIS_SOURCE_DIR':
				return src.source_dir

		run_step(cfg, step, src.source_dir, substitute, tool_pkgs, src.virtual_tools)

	src.mark_as_regenerated()

# ---------------------------------------------------------------------------------------
# Tool building.
# ---------------------------------------------------------------------------------------

def configure_tool(cfg, pkg):
	src = cfg.get_source(pkg.source)

	try_rmtree(pkg.build_dir)
	try_mkdir(pkg.build_dir, True)

	for step in pkg.configure_steps:
		tool_pkgs = []
		for dep_name in pkg.tool_dependencies:
			tool_pkgs.append(cfg.get_tool_pkg(dep_name))

		def substitute(varname):
			if varname == 'SOURCE_ROOT':
				return cfg.source_root
			elif varname == 'BUILD_ROOT':
				return cfg.build_root
			elif varname == 'SYSROOT_DIR':
				return cfg.sysroot_dir
			elif varname == 'THIS_SOURCE_DIR':
				return src.source_dir
			elif varname == 'THIS_BUILD_DIR':
				return pkg.build_dir
			elif varname == 'PREFIX':
				return pkg.prefix_dir
			elif varname == 'PARALLELISM':
				nthreads = get_concurrency()
				return str(nthreads)

		run_step(cfg, step, pkg.build_dir, substitute, tool_pkgs, pkg.virtual_tools)

	pkg.mark_as_configured()

def compile_tool_stage(cfg, stage):
	pkg = stage.pkg
	src = cfg.get_source(pkg.source)

	for step in stage.compile_steps:
		tool_pkgs = []
		for dep_name in pkg.tool_dependencies:
			tool_pkgs.append(cfg.get_tool_pkg(dep_name))

		def substitute(varname):
			if varname == 'SOURCE_ROOT':
				return cfg.source_root
			elif varname == 'BUILD_ROOT':
				return cfg.build_root
			elif varname == 'SYSROOT_DIR':
				return cfg.sysroot_dir
			elif varname == 'THIS_SOURCE_DIR':
				return src.source_dir
			elif varname == 'THIS_BUILD_DIR':
				return pkg.build_dir
			elif varname == 'PREFIX':
				return pkg.prefix_dir
			elif varname == 'PARALLELISM':
				nthreads = get_concurrency()
				return str(nthreads)

		run_step(cfg, step, pkg.build_dir, substitute, tool_pkgs, pkg.virtual_tools)

	stage.mark_as_compiled()

def install_tool_stage(cfg, stage):
	pkg = stage.pkg
	src = cfg.get_source(pkg.source)

	try_mkdir(cfg.tool_out_dir)
#	try_rmtree(pkg.prefix_dir)
	try_mkdir(pkg.prefix_dir)

	for step in stage.install_steps:
		tool_pkgs = []
		for dep_name in pkg.tool_dependencies:
			tool_pkgs.append(cfg.get_tool_pkg(dep_name))

		def substitute(varname):
			if varname == 'SOURCE_ROOT':
				return cfg.source_root
			elif varname == 'BUILD_ROOT':
				return cfg.build_root
			elif varname == 'SYSROOT_DIR':
				return cfg.sysroot_dir
			elif varname == 'THIS_SOURCE_DIR':
				return src.source_dir
			elif varname == 'THIS_BUILD_DIR':
				return pkg.build_dir
			elif varname == 'PREFIX':
				return pkg.prefix_dir

		run_step(cfg, step, pkg.build_dir, substitute, tool_pkgs, pkg.virtual_tools)

	stage.mark_as_installed()

def archive_tool(cfg, tool):
	with tarfile.open(tool.archive_file, 'w|gz') as tar:
		for ent in os.listdir(tool.prefix_dir):
			tar.add(os.path.join(tool.prefix_dir, ent), arcname=ent)

# ---------------------------------------------------------------------------------------
# Package building.
# ---------------------------------------------------------------------------------------

def configure_pkg(cfg, pkg):
	src = cfg.get_source(pkg.source)

	try_rmtree(pkg.build_dir)
	try_mkdir(pkg.build_dir, True)

	for step in pkg.configure_steps:
		tool_pkgs = []
		for dep_name in pkg.tool_dependencies:
			tool_pkgs.append(cfg.get_tool_pkg(dep_name))

		def substitute(varname):
			if varname == 'SOURCE_ROOT':
				return cfg.source_root
			elif varname == 'BUILD_ROOT':
				return cfg.build_root
			elif varname == 'SYSROOT_DIR':
				return cfg.sysroot_dir
			elif varname == 'THIS_SOURCE_DIR':
				return src.source_dir
			elif varname == 'THIS_BUILD_DIR':
				return pkg.build_dir
			elif varname == 'PARALLELISM':
				nthreads = get_concurrency()
				return str(nthreads)

		run_step(cfg, step, pkg.build_dir, substitute, tool_pkgs, pkg.virtual_tools,
				for_package=True)

	pkg.mark_as_configured()

def build_pkg(cfg, pkg, reproduce=False):
	src = cfg.get_source(pkg.source)

	try_mkdir(cfg.package_out_dir)
	try_rmtree(pkg.collect_dir)
	os.mkdir(pkg.collect_dir)

	for step in pkg.build_steps:
		tool_pkgs = []
		for dep_name in pkg.tool_dependencies:
			tool_pkgs.append(cfg.get_tool_pkg(dep_name))

		def substitute(varname):
			if varname == 'SOURCE_ROOT':
				return cfg.source_root
			elif varname == 'BUILD_ROOT':
				return cfg.build_root
			elif varname == 'SYSROOT_DIR':
				return cfg.sysroot_dir
			elif varname == 'THIS_SOURCE_DIR':
				return src.source_dir
			elif varname == 'THIS_BUILD_DIR':
				return pkg.build_dir
			elif varname == 'THIS_COLLECT_DIR':
				return pkg.collect_dir
			elif varname == 'PARALLELISM':
				nthreads = get_concurrency()
				return str(nthreads)

		run_step(cfg, step, pkg.build_dir, substitute, tool_pkgs, pkg.virtual_tools,
				for_package=True)

	postprocess_libtool(cfg, pkg)

	if not reproduce:
		try_rmtree(pkg.staging_dir)
		os.rename(pkg.collect_dir, pkg.staging_dir)
	else:
		def discover_dirtree(root):
			s = set()

			def recurse(subdir=''):
				for dent in os.scandir(os.path.join(root, subdir)):
					path = os.path.join(subdir, dent.name)
					s.add(path)
					if dent.is_dir(follow_symlinks=False):
						recurse(path)

			recurse()
			return s

		repro_paths = discover_dirtree(pkg.collect_dir)
		exist_paths = discover_dirtree(pkg.staging_dir)

		repro_only = repro_paths.difference(exist_paths)
		exist_only = exist_paths.difference(repro_paths)
		if repro_only:
			raise RuntimeError("Paths {} only exist in reproducted build".format(
					', '.join(repro_only)))
		if exist_only:
			raise RuntimeError("Paths {} only exist in existing build".format(
					', '.join(repro_only)))

		any_issues = False
		for path in repro_paths:
			repro_stat = os.stat(os.path.join(pkg.collect_dir, path))
			exist_stat = os.stat(os.path.join(pkg.collect_dir, path))

			if stat.S_IFMT(repro_stat.st_mode) != stat.S_IFMT(exist_stat.st_mode):
				print("{}xbstrap{}: File type mismatch in file {}".format(
						colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
						path))
				any_issues = True
				continue

			if stat.S_ISREG(repro_stat.st_mode):
				if not filecmp.cmp(os.path.join(pkg.collect_dir, path),
						os.path.join(pkg.staging_dir, path),
						shallow=False):
					print("{}xbstrap{}: Content mismatch in file {}".format(
							colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
							path))
					any_issues = True
					continue

		if not any_issues:
			print("{}xbstrap{}: Build was reproduced exactly".format(
					colorama.Style.BRIGHT, colorama.Style.RESET_ALL))
		else:
			raise RuntimeError('Could not reproduce all files')

def pack_pkg(cfg, pkg, reproduce=False):
	if cfg.use_xbps:
		try_mkdir(cfg.xbps_repository_dir)

		output = subprocess.DEVNULL
		if verbosity:
			output = None

		args = ['xbps-create', '-A', 'x86_64',
			'-s', pkg.name,
			'-n', '{}-{}'.format(pkg.name, pkg.version),
			'-D', pkg.xbps_dependency_string(),
			pkg.staging_dir
		]

		xbps_file = '{}-{}.x86_64.xbps'.format(pkg.name, pkg.version)

		if not reproduce:
			print("{}xbstrap{}: Running {}".format(
					colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
					args))
			subprocess.call(args, cwd=cfg.xbps_repository_dir, stdout=output)

			args = ['xbps-rindex', '-fa',
					os.path.join(cfg.xbps_repository_dir, xbps_file)
			]
			print("{}xbstrap{}: Running {}".format(
					colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
					args))
			subprocess.call(args, stdout=output)
		else:
			print("{}xbstrap{}: Running {}".format(
					colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
					args))
			subprocess.call(args, cwd=cfg.package_out_dir, stdout=output)

			if not filecmp.cmp(os.path.join(cfg.package_out_dir, xbps_file),
					os.path.join(cfg.xbps_repository_dir, xbps_file),
					shallow=False):
				print("{}xbstrap{}: Mismatch in {}".format(
						colorama.Style.BRIGHT, colorama.Style.RESET_ALL, xbps_file))
				raise RuntimeError('Could not reproduce pack')

			print("{}xbstrap{}: Pack was reproduced exactly".format(
					colorama.Style.BRIGHT, colorama.Style.RESET_ALL))
	else:
		raise RuntimeError('Package management configuration does not support pack')

def install_pkg(cfg, pkg):
	# constraint: the sysroot directory must be located in the build root
	try_mkdir(cfg.sysroot_dir)

	if cfg.use_xbps:
		output = subprocess.DEVNULL
		if verbosity:
			output = None

		args = ['xbps-install', '-fy',
			'-r', cfg.sysroot_dir,
			'--repository', cfg.xbps_repository_dir,
			pkg.name
		]
		print("{}xbstrap{}: Running {}".format(
				colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
				args))
		subprocess.check_call(args, stdout=output)
	else:
		installtree(pkg.staging_dir, cfg.sysroot_dir)
		pkg.mark_as_installed()

def archive_pkg(cfg, pkg):
	with tarfile.open(pkg.archive_file, 'w|gz') as tar:
		for ent in os.listdir(pkg.staging_dir):
			tar.add(os.path.join(pkg.staging_dir, ent), arcname=ent)

def run_task(cfg, task):
	tools_required = []
	for dep_name in task.tool_dependencies:
		tools_required.append(cfg.get_tool_pkg(dep_name))

	def substitute(varname):
		if varname == 'SOURCE_ROOT':
			return cfg.source_root
		elif varname == 'BUILD_ROOT':
			return cfg.build_root
		elif varname == 'SYSROOT_DIR':
			return cfg.sysroot_dir
		elif varname == 'PARALLELISM':
			nthreads = get_concurrency()
			return str(nthreads)

	run_step(cfg, task.script_step, cfg.source_root, substitute, tools_required, task.virtual_tools,
			for_package=False)


def run_pkg_task(cfg, task):
	src = cfg.get_source(task.pkg.source)

	tools_required = []
	for dep_name in task.pkg.tool_dependencies:
		tools_required.append(cfg.get_tool_pkg(dep_name))

	def substitute(varname):
		if varname == 'SOURCE_ROOT':
			return cfg.source_root
		elif varname == 'BUILD_ROOT':
			return cfg.build_root
		elif varname == 'SYSROOT_DIR':
			return cfg.sysroot_dir
		elif varname == 'THIS_SOURCE_DIR':
			return src.source_dir
		elif varname == 'THIS_BUILD_DIR':
			return task._pkg.build_dir
		elif varname == 'PREFIX':
			return task._pkg.prefix_dir
		elif varname == 'PARALLELISM':
			nthreads = get_concurrency()
			return str(nthreads)

	run_step(cfg, task.script_step, task.pkg.build_dir, substitute, tools_required,
			task.pkg.virtual_tools, for_package=False)

def run_tool_task(cfg, task):
	src = cfg.get_source(task.pkg.source)

	tools_required = []
	for dep_name in task.pkg.tool_dependencies:
		tools_required.append(cfg.get_tool_pkg(dep_name))

	def substitute(varname):
		if varname == 'SOURCE_ROOT':
			return cfg.source_root
		elif varname == 'BUILD_ROOT':
			return cfg.build_root
		elif varname == 'SYSROOT_DIR':
			return cfg.sysroot_dir
		elif varname == 'THIS_SOURCE_DIR':
			return src.source_dir
		elif varname == 'THIS_BUILD_DIR':
			return task._pkg.build_dir
		elif varname == 'PREFIX':
			return task._pkg.prefix_dir
		elif varname == 'PARALLELISM':
			nthreads = get_concurrency()
			return str(nthreads)

	run_step(cfg, task.script_step, task.pkg.build_dir, substitute, tools_required,
			task.pkg.virtual_tools, for_package=False)

# ---------------------------------------------------------------------------------------
# Build planning.
# ---------------------------------------------------------------------------------------

class Action(Enum):
	NULL = 0
	# Source-related actions.
	FETCH_SRC = 1
	CHECKOUT_SRC = 2
	PATCH_SRC = 3
	REGENERATE_SRC = 4
	# Tool-related actions.
	CONFIGURE_TOOL = 5
	COMPILE_TOOL_STAGE = 6
	INSTALL_TOOL_STAGE = 7
	# Package-related actions.
	CONFIGURE_PKG = 8
	BUILD_PKG = 9
	REPRODUCE_BUILD_PKG = 10
	PACK_PKG = 11
	REPRODUCE_PACK_PKG = 12
	INSTALL_PKG = 13
	ARCHIVE_TOOL = 14
	ARCHIVE_PKG = 15
	RUN = 16
	RUN_PKG = 17
	RUN_TOOL = 18
	WANT_TOOL = 19
	WANT_PKG = 20

Action.strings = {
	Action.FETCH_SRC: 'fetch',
	Action.CHECKOUT_SRC: 'checkout',
	Action.PATCH_SRC: 'patch',
	Action.REGENERATE_SRC: 'regenerate',
	Action.CONFIGURE_TOOL: 'configure-tool',
	Action.COMPILE_TOOL_STAGE: 'compile-tool',
	Action.INSTALL_TOOL_STAGE: 'install-tool',
	Action.CONFIGURE_PKG: 'configure',
	Action.BUILD_PKG: 'build',
	Action.REPRODUCE_BUILD_PKG: 'reproduce-build',
	Action.PACK_PKG: 'pack',
	Action.REPRODUCE_PACK_PKG: 'reproduce-pack',
	Action.INSTALL_PKG: 'install',
	Action.ARCHIVE_TOOL: 'archive-tool',
	Action.ARCHIVE_PKG: 'archive',
	Action.RUN: 'run',
	Action.RUN_PKG: 'run',
	Action.RUN_TOOL: 'run',
	Action.WANT_TOOL: 'want-tool',
	Action.WANT_PKG: 'want-pkg',
}

class PlanState(Enum):
	NULL = 0
	EXPANDING = 1
	ORDERED = 2

class ExecutionStatus(Enum):
	NULL = 0
	SUCCESS = 1
	STEP_FAILED = 2
	PREREQS_FAILED = 3
	NOT_WANTED = 4

class PlanItem:
	def __init__(self, action, subject, settings):
		self.action = action
		self.subject = subject
		self.settings = settings

		self._state = None
		self.active = False
		self.build_edges = set()
		self.require_edges = set()
		self.order_before_edges = set()
		self.order_after_edges = set()

		self.plan_state = PlanState.NULL
		self.edge_list = []
		self.resolved_n = 0 # Number of resolved edges.
		self.build_span = False
		self.outdated = False

		self.exec_status = ExecutionStatus.NULL

	@property
	def is_missing(self):
		self._determine_state()
		return self._state.missing

	@property
	def is_updatable(self):
		self._determine_state()
		return self._state.updatable

	@property
	def timestamp(self):
		self._determine_state()
		return self._state.timestamp

	def _determine_state(self):
		if self._state is not None:
			return
		visitors = {
			Action.FETCH_SRC: lambda s, c: s.check_if_fetched(c),
			Action.CHECKOUT_SRC: lambda s, c: s.check_if_checkedout(c),
			Action.PATCH_SRC: lambda s, c: s.check_if_patched(c),
			Action.REGENERATE_SRC: lambda s, c: s.check_if_regenerated(c),
			Action.CONFIGURE_TOOL: lambda s, c: s.check_if_configured(c),
			Action.COMPILE_TOOL_STAGE: lambda s, c: s.check_if_compiled(c),
			Action.INSTALL_TOOL_STAGE: lambda s, c: s.check_if_installed(c),
			Action.CONFIGURE_PKG: lambda s, c: s.check_if_configured(c),
			Action.BUILD_PKG: lambda s, c: s.check_staging(c),
			Action.REPRODUCE_BUILD_PKG: lambda s, c: ItemState(missing=True),
			Action.PACK_PKG: lambda s, c: s.check_if_packed(c),
			Action.REPRODUCE_PACK_PKG: lambda s, c: ItemState(missing=True),
			Action.INSTALL_PKG: lambda s, c: s.check_if_installed(c),
			Action.ARCHIVE_TOOL: lambda s, c: s.check_if_archived(c),
			Action.ARCHIVE_PKG: lambda s, c: ItemState(missing=True),
			Action.RUN: lambda s, c: ItemState(missing=True),
			Action.RUN_PKG: lambda s, c: ItemState(missing=True),
			Action.RUN_TOOL: lambda s, c: ItemState(missing=True),
			Action.WANT_TOOL: lambda s, c: s.check_if_fully_installed(c),
			Action.WANT_PKG: lambda s, c: s.check_staging(c),
		}
		self._state = visitors[self.action](self.subject, self.settings)

class ExecutionFailureException(Exception):
	def __init__(self, step, subject):
		super().__init__("Action {} of {} {} failed".format(Action.strings[step],
				subject.subject_type, subject.subject_id))
		self.step = step
		self.subject = subject

class PlanFailureException(Exception):
	def __init__(self):
		super().__init__("Plan failed")

class Plan:
	def __init__(self, cfg):
		self._cfg = cfg
		self._order = []
		self._visited_for_materialization = set()
		self._visited_for_activation = set()
		self._items = dict()
		self._stack = []
		self._settings = None
		self.build_scope = None
		self.dry_run = False
		self.check = False
		self.update = False
		self.recursive = False
		self.paranoid = False
		self.wanted = set()
		self.reset = ResetMode.NONE
		self.hard = False
		self.only_wanted = False
		self.keep_going = False
		self.progress_file = None

	@property
	def cfg(self):
		return self._cfg

	def _materialize_item(self, action, subject):
		item = PlanItem(action, subject, self._settings)

		def add_implicit_pkgs():
			if not subject.is_implicit:
				for implicit in self._cfg.all_pkgs():
					if implicit.is_implicit:
						item.require_edges.add((action.INSTALL_PKG, implicit))

		def add_source_dependencies(s):
			for src_name in s.source_dependencies:
				dep_source = self._cfg.get_source(src_name)
				item.require_edges.add((action.PATCH_SRC, dep_source))

		def add_tool_dependencies(s):
			for (tool_name, stage_name) in s.tool_stage_dependencies:
				dep_tool = self._cfg.get_tool_pkg(tool_name)
				if self.build_scope is not None and dep_tool not in self.build_scope:
					item.require_edges.add((action.WANT_TOOL, dep_tool))
				else:
					tool_stage = dep_tool.get_stage(stage_name)
					item.require_edges.add((action.INSTALL_TOOL_STAGE, tool_stage))

		def add_pkg_dependencies(s):
			for pkg_name in s.pkg_dependencies:
				dep_pkg = self._cfg.get_target_pkg(pkg_name)
				item.require_edges.add((action.INSTALL_PKG, dep_pkg))

		def add_task_dependencies(s):
			for task_name in s.task_dependencies:
				dep_task = self._cfg.get_task(task_name)
				item.require_edges.add((action.RUN, dep_task))
			for task_name in s.tasks_ordered_before:
				dep_task = self._cfg.get_task(task_name)
				item.order_before_edges.add((action.RUN, dep_task))

		sid = subject.subject_id

		if action == Action.FETCH_SRC:
			# FETCH_SRC has no dependencies.
			pass

		elif action == Action.CHECKOUT_SRC:
			item.build_edges.add((action.FETCH_SRC, subject))

		elif action == Action.PATCH_SRC:
			item.build_edges.add((action.CHECKOUT_SRC, subject))

		elif action == Action.REGENERATE_SRC:
			item.build_edges.add((action.PATCH_SRC, subject))

			add_source_dependencies(subject)
			add_tool_dependencies(subject)

		elif action == Action.CONFIGURE_TOOL:
			src = self._cfg.get_source(subject.source)
			item.build_edges.add((action.REGENERATE_SRC, src))

			add_source_dependencies(subject)
			add_tool_dependencies(subject)
			add_pkg_dependencies(subject)
			add_task_dependencies(subject)

		elif action == Action.COMPILE_TOOL_STAGE:
			item.build_edges.add((action.CONFIGURE_TOOL, subject.pkg))

			add_source_dependencies(subject)
			add_tool_dependencies(subject.pkg)
			add_tool_dependencies(subject)
			add_pkg_dependencies(subject)
			add_task_dependencies(subject)

		elif action == Action.INSTALL_TOOL_STAGE:
			item.build_edges.add((action.COMPILE_TOOL_STAGE, subject))

			add_tool_dependencies(subject.pkg)
			add_tool_dependencies(subject)
			add_pkg_dependencies(subject)
			add_task_dependencies(subject)

		elif action == Action.CONFIGURE_PKG:
			src = self._cfg.get_source(subject.source)
			item.build_edges.add((action.REGENERATE_SRC, src))

			# Configuration requires all dependencies to be present.
			add_source_dependencies(subject)
			add_implicit_pkgs()
			add_pkg_dependencies(subject)
			add_tool_dependencies(subject)
			add_task_dependencies(subject)

		elif action == Action.BUILD_PKG or action == Action.REPRODUCE_BUILD_PKG:
			item.build_edges.add((action.CONFIGURE_PKG, subject))

			# Usually dependencies will already be installed during the configuration phase.
			# However, if the sysroot is removed, we might need to install again.
			add_source_dependencies(subject)
			add_implicit_pkgs()
			add_pkg_dependencies(subject)
			add_tool_dependencies(subject)
			add_task_dependencies(subject)

		elif action == Action.PACK_PKG or action == Action.REPRODUCE_PACK_PKG:
			item.build_edges.add((action.BUILD_PKG, subject))

		elif action == Action.INSTALL_PKG:
			if self.build_scope is not None and subject not in self.build_scope:
				item.build_edges.add((action.WANT_PKG, subject))
			elif self._cfg.use_xbps:
				item.build_edges.add((action.PACK_PKG, subject))
			else:
				item.build_edges.add((action.BUILD_PKG, subject))

			# See Action.BUILD_PKG for rationale.
			add_implicit_pkgs()
			add_pkg_dependencies(subject)

		elif action == Action.ARCHIVE_TOOL:
			for stage in subject.all_stages():
				item.build_edges.add((action.INSTALL_TOOL_STAGE, stage))

		elif action == Action.ARCHIVE_PKG:
			item.build_edges.add((action.BUILD_PKG, subject))

		elif action == Action.RUN:
			add_implicit_pkgs()
			add_pkg_dependencies(subject)
			add_tool_dependencies(subject)
			add_task_dependencies(subject)

		elif action == Action.RUN_PKG:
			item.build_edges.add((action.BUILD_PKG, subject.pkg))
			add_implicit_pkgs()
			add_pkg_dependencies(subject)
			add_tool_dependencies(subject)
			add_task_dependencies(subject)

		elif action == Action.RUN_TOOL:
			for stage in subject.pkg.all_stages():
				item.build_edges.add((Action.COMPILE_TOOL_STAGE, stage))

			add_tool_dependencies(subject.pkg)
			add_tool_dependencies(subject)
			add_pkg_dependencies(subject)
			add_task_dependencies(subject)

		return item

	def _do_materialization_visit(self, edges):
		for edge_pair in edges:
			if edge_pair in self._visited_for_materialization:
				continue
			self._visited_for_materialization.add(edge_pair)
			self._stack.append(edge_pair)

	def _do_order_before(self, item, edges):
		for pair in edges:
			if pair not in self._items:
				continue
			edge_item = self._items[pair]
			item.edge_list.append(pair)

	def _do_materialization(self):
		# First, call _materialize_item() on all (action, subject) pairs.
		assert not self._stack
		self._visited_for_materialization.update(self.wanted)
		self._stack.extend(self._visited_for_materialization)

		while self._stack:
			(action, subject) = self._stack.pop()

			# TODO: Store the subject.subject_id instead of the subject object (= the package)?
			item = self._materialize_item(action, subject)
			self._items[(action, subject)] = item

			self._do_materialization_visit(item.build_edges)
			self._do_materialization_visit(item.require_edges)

	def _do_ordering(self):
		# Resolve ordering edges.
		for item in self._items.values():
			self._do_order_before(item, item.build_edges)
			self._do_order_before(item, item.require_edges)
			self._do_order_before(item, item.order_before_edges)

			for pair in item.order_after_edges:
				target_item = self._items[pair]
				target_item.edge_list.append((item.action, item.subject))

		# The following code does a topologic sort of the desired items.
		stack = []

		def visit(item):
			if item.plan_state == PlanState.NULL:
				item.plan_state = PlanState.EXPANDING
				stack.append(item)
			elif item.plan_state == PlanState.EXPANDING:
				for circ_item in stack:
					print(Action.strings[circ_item.action], circ_item.subject.subject_id)
				raise RuntimeError("Package has circular dependencies")
			else:
				# Packages that are already ordered do not need to be considered again.
				assert item.plan_state == PlanState.ORDERED

		for root_item in self._items.values():
			visit(root_item)

			while stack:
				item = stack[-1]
				(action, subject) = (item.action, item.subject)
				if item.resolved_n == len(item.edge_list):
					assert item.plan_state == PlanState.EXPANDING
					item.plan_state = PlanState.ORDERED
					stack.pop()
					self._order.append((action, subject))
				else:
					edge_item = self._items[item.edge_list[item.resolved_n]]
					item.resolved_n += 1
					visit(edge_item)

	def _do_activation(self):
		# Determine the items that will be enabled.
		def visit(edges):
			for pair in edges:
				if pair in self._visited_for_activation:
					continue
				action, subject = pair
				item = self._items[pair]
				self._visited_for_activation.add(pair)
				if item.is_missing:
					self._stack.append(pair)

		def activate(root_action, root_subject):
			assert not self._stack
			self._visited_for_activation.add((root_action, root_subject))
			self._stack.append((root_action, root_subject))

			while self._stack:
				pair = self._stack.pop()
				item = self._items[pair]
				if item.active:
					continue
				item.active = True

				visit(item.build_edges)
				visit(item.require_edges)

		# Activate wanted items.
		for (action, subject) in self.wanted:
			item = self._items[(action, subject)]
			item.build_span = True
			if not self.check or item.is_missing:
				activate(action, subject)

		# Discover all items reachable by build edges.
		for (action, subject) in reversed(self._order):
			item = self._items[(action, subject)]
			if not item.build_span:
				continue
			for dep_pair in item.build_edges:
				dep_item = self._items[dep_pair]
				dep_item.build_span = True

		# Activate updatable items.
		if self.update:
			def is_outdated(item, dep_item):
				ts = item.timestamp
				dep_ts = dep_item.timestamp
				if ts is None or dep_ts is None:
					return False
				return dep_ts > ts

			for (action, subject) in self._order:
				item = self._items[(action, subject)]
				# Unless we're doing a recursive update, we only follow check items
				# that are reachable by build edges.
				if not self.recursive and not item.build_span:
					continue
				if item.is_missing or item.is_updatable:
					activate(action, subject)

				# Activate items if their dependencies were activated.
				for dep_pair in item.build_edges:
					dep_item = self._items[dep_pair]
					if dep_item.active:
						activate(action, subject)
					elif is_outdated(item, dep_item):
						item.outdated = True
						activate(action, subject)
				if self.recursive:
					for dep_pair in item.require_edges:
						dep_item = self._items[dep_pair]
						if dep_item.active:
							activate(action, subject)
						elif is_outdated(item, dep_item):
							item.outdated = True
							activate(action, subject)

	def compute_plan(self, no_ordering=False, no_activation=False):
		self._do_materialization()
		if no_ordering:
			return
		self._do_ordering()
		if no_activation:
			return
		self._do_activation()

	def materialized_steps(self):
		return self._items.keys()

	def run_plan(self):
		self._settings = ItemSettings()
		if self.update:
			self._settings.check_remotes = 1
		if self.update and self.paranoid:
			self._settings.check_remotes = 2
		self._settings.reset = self.reset

		# Compute the plan.
		self._do_materialization()
		self._do_ordering()
		self._do_activation()

		# Run the plan.
		scheduled = [(action, subject) for (action, subject) in self._order
				if self._items[(action, subject)].active]

		if scheduled:
			print('{}xbstrap{}: Running the following plan:'.format(
					colorama.Style.BRIGHT, colorama.Style.RESET_ALL))
		else:
			print('{}xbstrap{}: Nothing to do'.format(
					colorama.Style.BRIGHT, colorama.Style.RESET_ALL))
		for (action, subject) in scheduled:
			if isinstance(subject, HostStage):
				if subject.stage_name:
					print('    {:14} {}, stage: {}'.format(Action.strings[action],
							subject.pkg.name, subject.stage_name), end='')
				else:
					print('    {:14} {}'.format(Action.strings[action], subject.pkg.name), end='')
			else:
				print('    {:14} {}'.format(Action.strings[action], subject.name), end='')
			if self._items[(action, subject)].is_updatable:
				print(' ({}{}updatable{})'.format(colorama.Style.BRIGHT, colorama.Fore.BLUE,
						colorama.Style.RESET_ALL), end='')
			elif self._items[(action, subject)].outdated:
				print(' ({}{}outdated{})'.format(colorama.Style.BRIGHT, colorama.Fore.BLUE,
						colorama.Style.RESET_ALL), end='')
			print()

		if self.dry_run:
			return

		any_failed_items = False
		for (n, (action, subject)) in enumerate(scheduled):
			item = self._items[(action, subject)]

			# Check if any prerequisites failed; this can generally only happen with --keep-going.
			any_failed_edges = False
			for edge_pair in item.edge_list:
				edge_item = self._items[edge_pair]
				if not edge_item.active:
					continue
				assert edge_item.exec_status != ExecutionStatus.NULL
				if edge_item.exec_status != ExecutionStatus.SUCCESS:
					any_failed_edges = True

			def emit_progress(status):
				if self.progress_file is not None:
					yml = {
						'n_this': n + 1,
						'n_all': len(scheduled),
						'status': status,
						'action': Action.strings[action],
						'subject': subject.subject_id,
						'artifact_files': []
					}
					if action == Action.RUN:
						for af in subject.artifact_files:
							yml['artifact_files'].append({'name': af.name, 'filepath': af.filepath})
					self.progress_file.write(yaml.safe_dump(yml, explicit_end=True))
					self.progress_file.flush()

			if self.keep_going and any_failed_edges:
				print('{}xbstrap{}: Skipping action {} of {} due to failed prerequisites [{}/{}]'.format(
						colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
						Action.strings[action], subject.subject_id,
						n + 1, len(scheduled)))
				item.exec_status = ExecutionStatus.PREREQS_FAILED
				emit_progress('prereqs-failed')
				any_failed_items = True
				continue

			if self.only_wanted and (action, subject) not in self.wanted:
				if not self.keep_going:
					raise ExecutionFailureException(action, subject)
				item.exec_status = ExecutionStatus.NOT_WANTED
				emit_progress('not-wanted')
				any_failed_items = True
				continue

			assert not any_failed_edges
			print('{}xbstrap{}: {} {} [{}/{}]'.format(
					colorama.Style.BRIGHT, colorama.Style.RESET_ALL,
					Action.strings[action], subject.subject_id,
					n + 1, len(scheduled)))
			try:
				if action == Action.FETCH_SRC:
					fetch_src(self._cfg, subject)
				elif action == Action.CHECKOUT_SRC:
					checkout_src(self._cfg, subject, self._settings)
				elif action == Action.PATCH_SRC:
					patch_src(self._cfg, subject)
				elif action == Action.REGENERATE_SRC:
					regenerate_src(self._cfg, subject)
				elif action == Action.CONFIGURE_TOOL:
					configure_tool(self._cfg, subject)
				elif action == Action.COMPILE_TOOL_STAGE:
					compile_tool_stage(self._cfg, subject)
				elif action == Action.INSTALL_TOOL_STAGE:
					install_tool_stage(self._cfg, subject)
				elif action == Action.CONFIGURE_PKG:
					configure_pkg(self._cfg, subject)
				elif action == Action.BUILD_PKG:
					build_pkg(self._cfg, subject)
				elif action == Action.REPRODUCE_BUILD_PKG:
					build_pkg(self._cfg, subject, reproduce=True)
				elif action == Action.PACK_PKG:
					pack_pkg(self._cfg, subject)
				elif action == Action.REPRODUCE_PACK_PKG:
					pack_pkg(self._cfg, subject, reproduce=True)
				elif action == Action.INSTALL_PKG:
					install_pkg(self._cfg, subject)
				elif action == Action.ARCHIVE_TOOL:
					archive_tool(self._cfg, subject)
				elif action == Action.ARCHIVE_PKG:
					archive_pkg(self._cfg, subject)
				elif action == Action.RUN:
					run_task(self._cfg, subject)
				elif action == Action.RUN_PKG:
					run_pkg_task(self._cfg, subject)
				elif action == Action.RUN_TOOL:
					run_tool_task(self._cfg, subject)
				elif action == Action.WANT_TOOL:
					# 'want' actions denote dependencies outside of the build scope.
					# If they are activated, the plan fails unconditionally.
					raise ExecutionFailureException(action, subject)
				elif action == Action.WANT_PKG:
					# 'want' actions denote dependencies outside of the build scope.
					# If they are activated, the plan fails unconditionally.
					raise ExecutionFailureException(action, subject)
				else:
					raise AssertionError("Unexpected action")
				item.exec_status = ExecutionStatus.SUCCESS
				emit_progress('success')
			except (subprocess.CalledProcessError, ExecutionFailureException):
				item.exec_status = ExecutionStatus.STEP_FAILED
				emit_progress('failure')
				if not self.keep_going:
					raise ExecutionFailureException(action, subject)
				any_failed_items = True

		if any_failed_items:
			print('{}xbstrap{}: The following steps failed:'.format(
					colorama.Style.BRIGHT, colorama.Style.RESET_ALL))
			for (action, subject) in scheduled:
				item = self._items[(action, subject)]
				assert item.exec_status != ExecutionStatus.NULL
				if item.exec_status == ExecutionStatus.SUCCESS:
					continue

				if isinstance(subject, HostStage):
					if subject.stage_name:
						print('    {:14} {}, stage: {}'.format(Action.strings[action],
								subject.pkg.name, subject.stage_name), end='')
					else:
						print('    {:14} {}'.format(Action.strings[action], subject.pkg.name), end='')
				else:
					print('    {:14} {}'.format(Action.strings[action], subject.name), end='')
				if item.exec_status == ExecutionStatus.PREREQS_FAILED:
					print(' (prerequisites failed)', end='')
				elif item.exec_status == ExecutionStatus.NOT_WANTED:
					print(' (not wanted)', end='')
				print()

			raise PlanFailureException()
