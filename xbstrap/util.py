# SPDX-License-Identifier: MIT

import contextlib
import errno
import fcntl
import os
import os.path as path
import re
import sys
import urllib.parse
import urllib.request

import colorama


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
