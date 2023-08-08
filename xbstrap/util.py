# SPDX-License-Identifier: MIT

import contextlib
import errno
import fcntl
import os
import os.path as path
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
