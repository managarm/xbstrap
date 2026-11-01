# SPDX-License-Identifier: MIT

import contextlib
import errno
import fcntl
import json
import os
import os.path as path
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid

import colorama

from xbstrap.exceptions import GenericError


def eprint(*args, **kwargs):
    return print(*args, **kwargs, file=sys.stderr)


def log_info(msg):
    eprint("{}xbstrap{}: {}".format(colorama.Style.BRIGHT, colorama.Style.RESET_ALL, msg))


def log_warn(msg):
    eprint(
        "{}xbstrap{}: {}{}{}".format(
            colorama.Style.BRIGHT,
            colorama.Style.NORMAL,
            colorama.Fore.YELLOW,
            msg,
            colorama.Style.RESET_ALL,
        )
    )


def log_err(msg):
    eprint(
        "{}xbstrap{}: {}{}{}".format(
            colorama.Style.BRIGHT,
            colorama.Style.NORMAL,
            colorama.Fore.RED,
            msg,
            colorama.Style.RESET_ALL,
        ),
    )


def find_home():
    if "XBSTRAP_HOME" in os.environ:
        return os.environ["XBSTRAP_HOME"]
    return os.path.expanduser("~/.xbstrap")


def find_cache_dir():
    if "XBSTRAP_CACHE_DIR" in os.environ:
        return os.environ["XBSTRAP_CACHE_DIR"]
    return find_home()


def find_cbuildrt_workspace():
    return os.path.join(find_cache_dir(), "cbuildrt")


class CbuildrtWorkspace:
    __slots__ = ("path", "has_subids")

    def __init__(self, path, has_subids):
        self.path = path
        self.has_subids = has_subids


def xbstrap_bin_environ():
    environ = os.environ.copy()
    build_environ_paths(environ, "PATH", prepend=[os.path.join(find_home(), "bin")])
    return environ


def ensure_cbuildrt_workspace():
    workspace = find_cbuildrt_workspace()
    try_mkdir(os.path.dirname(workspace), recursive=True)
    meta_path = os.path.join(workspace, "workspace.json")
    if not os.path.exists(meta_path):
        subprocess.check_call(
            ["cbuildrt", "--workspace", workspace, "init"],
            env=xbstrap_bin_environ(),
        )
    with open(meta_path) as f:
        meta = json.load(f)
    has_subids = meta.get("subUid") is not None and meta.get("subGid") is not None
    return CbuildrtWorkspace(workspace, has_subids)


def list_cbuildrt_volumes():
    workspace = ensure_cbuildrt_workspace()
    try:
        out = subprocess.check_output(
            ["cbuildrt", "--workspace", workspace.path, "volume", "ls"],
            env=xbstrap_bin_environ(),
            encoding="utf-8",
        )
    except subprocess.CalledProcessError:
        raise GenericError(
            "cbuildrt volume ls failed; xbstrap needs a cbuildrt with volume support"
            " (try `xbstrap prereqs cbuildrt`)"
        )
    return out.split()


def remove_cbuildrt_volume(name):
    workspace = ensure_cbuildrt_workspace()
    subprocess.check_call(
        ["cbuildrt", "--workspace", workspace.path, "volume", "rm", name],
        env=xbstrap_bin_environ(),
    )


def _read_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, NotADirectoryError):
        return {}


def _write_json(path, data):
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=2)
    os.rename(path + ".tmp", path)


# The registry maps sysroot volumes to the sysroots that own them.
def find_sysroot_registry_dir():
    return os.path.join(find_cache_dir(), "sysroot_volumes")


def read_sysroot_registry():
    return _read_json(os.path.join(find_sysroot_registry_dir(), "registry.json"))


def write_sysroot_registry(registry):
    _write_json(os.path.join(find_sysroot_registry_dir(), "registry.json"), registry)


def _sysroot_volume_link(sysroot):
    # abspath() strips trailing slashes, which would put the link inside the sysroot.
    return os.path.abspath(sysroot) + ".volume"


def read_sysroot_volume(sysroot):
    # Returns the name of the sysroot's volume, or None if it has none.
    link = _sysroot_volume_link(sysroot)
    try:
        target = os.readlink(link)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise GenericError(f"Failed to read {link}: {e}")
    # cbuildrt would silently create an empty volume of the same name in another workspace.
    workspace = os.path.dirname(os.path.dirname(target))
    if os.path.realpath(workspace) != os.path.realpath(find_cbuildrt_workspace()):
        raise GenericError(
            f"{link} points to cbuildrt workspace {workspace},"
            f" but the current workspace is {find_cbuildrt_workspace()}"
        )
    return os.path.basename(target)


def is_sysroot_volume_linked(sysroot, name):
    # Unlike read_sysroot_volume(), this tolerates any state of the sysroot's build directory.
    # Volume names are unique, so a matching name is sufficient even if the workspace moved.
    try:
        return os.path.basename(os.readlink(_sysroot_volume_link(sysroot))) == name
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError as e:
        # Keep volumes that we cannot prove to be unlinked.
        log_warn(f"Keeping sysroot volume {name}: {e}")
        return True


def ensure_sysroot_volume(sysroot):
    # Returns the name of the sysroot's volume, assigning one on first use.
    sysroot = os.path.abspath(sysroot)
    name = "sysroot-" + uuid.uuid4().hex
    target = os.path.join(find_cbuildrt_workspace(), "volumes", name)
    try:
        # symlink() fails if the link exists, so concurrent callers agree on one volume.
        # Containerless steps only see the empty mount point at the sysroot itself.
        os.symlink(target, _sysroot_volume_link(sysroot))
    except FileExistsError:
        name = read_sysroot_volume(sysroot)

    # gc removes registered volumes that have no link, so register after linking.
    try_mkdir(find_sysroot_registry_dir(), recursive=True)
    with lock_directory(find_sysroot_registry_dir()):
        registry = read_sysroot_registry()
        if registry.get(name) != sysroot:
            # Other volumes of this sysroot belong to a deleted build directory.
            for stale in [v for v, owner in registry.items() if owner == sysroot]:
                log_info(f"Removing sysroot volume {stale} of a previous build in {sysroot}")
                try:
                    remove_cbuildrt_volume(stale)
                    del registry[stale]
                except subprocess.CalledProcessError:
                    log_warn(f"Failed to remove sysroot volume {stale}")
            registry[name] = sysroot
            write_sysroot_registry(registry)
    return name


def touchtree(root):
    for name in os.listdir(root):
        path = os.path.join(root, name)

        os.utime(path, (0, 0), follow_symlinks=False)

        if not os.path.islink(path) and os.path.isdir(path):
            touchtree(path)


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
    joined = ":".join(prepend)
    if varname in environ and environ[varname]:
        environ[varname] = joined + ":" + environ[varname]
    else:
        environ[varname] = joined


def interactive_download(url, path):
    istty = os.isatty(1)  # This is stdout.
    if istty:
        eprint("...", end="")  # This will become the status line.

    def show_progress(num_blocks, block_size, file_size):
        progress = min(num_blocks * block_size, file_size)
        rewind = ""
        newline = ""
        if istty:
            rewind = "\r"
        else:

            def discrete(n):
                return int(10 * n * block_size / file_size)

            if num_blocks > 0 and discrete(num_blocks - 1) == discrete(num_blocks):
                return
            newline = "\n"
        frac = progress / file_size
        eprint(
            "{}[{}{}]\x1b[K{:8.0f} KiB / {:8.0f} KiB, {:7.2f}%".format(
                rewind,
                "#" * int(20 * frac),
                " " * (20 - int(20 * frac)),
                progress / 1024,
                file_size / 1024,
                progress / file_size * 100,
            ),
            end=newline,
        )

    temp_path = path + ".download"
    urllib.request.urlretrieve(url, temp_path, show_progress)
    os.rename(temp_path, path)
    if istty:
        eprint()


@contextlib.contextmanager
def lock_directory(directory, mode=fcntl.LOCK_EX):
    try_mkdir(directory)
    fname = path.join(directory, ".xbstrap_lock")
    with open(fname, "w") as f:
        fcntl.flock(f.fileno(), mode)
        yield


# This is the glob.translate() function from Python 3.13+.
# This is the version from Python 3.14.
# Copyright (c) 2001-2025 Python Software Foundation
# TODO: Get rid of this copy when we can rely on Python 3.13+
def translate_glob(pat, *, recursive=False, include_hidden=False, seps=None):
    if not seps:
        if os.path.altsep:
            seps = (os.path.sep, os.path.altsep)
        else:
            seps = os.path.sep
    escaped_seps = "".join(map(re.escape, seps))
    any_sep = f"[{escaped_seps}]" if len(seps) > 1 else escaped_seps
    not_sep = f"[^{escaped_seps}]"
    if include_hidden:
        one_last_segment = f"{not_sep}+"
        one_segment = f"{one_last_segment}{any_sep}"
        any_segments = f"(?:.+{any_sep})?"
        any_last_segments = ".*"
    else:
        one_last_segment = f"[^{escaped_seps}.]{not_sep}*"
        one_segment = f"{one_last_segment}{any_sep}"
        any_segments = f"(?:{one_segment})*"
        any_last_segments = f"{any_segments}(?:{one_last_segment})?"

    results = []
    parts = re.split(any_sep, pat)
    last_part_idx = len(parts) - 1
    for idx, part in enumerate(parts):
        if part == "*":
            results.append(one_segment if idx < last_part_idx else one_last_segment)
        elif recursive and part == "**":
            if idx < last_part_idx:
                if parts[idx + 1] != "**":
                    results.append(any_segments)
            else:
                results.append(any_last_segments)
        else:
            if part:
                if not include_hidden and part[0] in "*?":
                    results.append(r"(?!\.)")
                results.extend(fnmatch_underscore_translate(part, f"{not_sep}*", not_sep)[0])
            if idx < last_part_idx:
                results.append(any_sep)
    res = "".join(results)
    return rf"(?s:{res})\Z"


_re_setops_sub = re.compile(r"([&~|])").sub


# This is fnmatch._translate() from Python 3.14.
# Copyright (c) 2001-2025 Python Software Foundation
def fnmatch_underscore_translate(pat, star, question_mark):
    res = []
    add = res.append
    star_indices = []

    i, n = 0, len(pat)
    while i < n:
        c = pat[i]
        i = i + 1
        if c == "*":
            # store the position of the wildcard
            star_indices.append(len(res))
            add(star)
            # compress consecutive `*` into one
            while i < n and pat[i] == "*":
                i += 1
        elif c == "?":
            add(question_mark)
        elif c == "[":
            j = i
            if j < n and pat[j] == "!":
                j = j + 1
            if j < n and pat[j] == "]":
                j = j + 1
            while j < n and pat[j] != "]":
                j = j + 1
            if j >= n:
                add("\\[")
            else:
                stuff = pat[i:j]
                if "-" not in stuff:
                    stuff = stuff.replace("\\", r"\\")
                else:
                    chunks = []
                    k = i + 2 if pat[i] == "!" else i + 1
                    while True:
                        k = pat.find("-", k, j)
                        if k < 0:
                            break
                        chunks.append(pat[i:k])
                        i = k + 1
                        k = k + 3
                    chunk = pat[i:j]
                    if chunk:
                        chunks.append(chunk)
                    else:
                        chunks[-1] += "-"
                    # Remove empty ranges -- invalid in RE.
                    for k in range(len(chunks) - 1, 0, -1):
                        if chunks[k - 1][-1] > chunks[k][0]:
                            chunks[k - 1] = chunks[k - 1][:-1] + chunks[k][1:]
                            del chunks[k]
                    # Escape backslashes and hyphens for set difference (--).
                    # Hyphens that create ranges shouldn't be escaped.
                    stuff = "-".join(s.replace("\\", r"\\").replace("-", r"\-") for s in chunks)
                i = j + 1
                if not stuff:
                    # Empty range: never match.
                    add("(?!)")
                elif stuff == "!":
                    # Negated empty range: match any character.
                    add(".")
                else:
                    # Escape set operations (&&, ~~ and ||).
                    stuff = _re_setops_sub(r"\\\1", stuff)
                    if stuff[0] == "!":
                        stuff = "^" + stuff[1:]
                    elif stuff[0] in ("^", "["):
                        stuff = "\\" + stuff
                    add(f"[{stuff}]")
        else:
            add(re.escape(c))
    assert i == n
    return res, star_indices
