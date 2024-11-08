import itertools
import plistlib
import shlex
import tarfile

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


class XbpsVersion:
    def __init__(self, comps, revision=1):
        self.comps = comps
        self.revision = revision


# This matches the version parsing algorithm in xbps.
def parse_components(v):
    modifiers = {
        "alpha": -3,
        "beta": -2,
        "pre": -1,
        "rc": -1,
        "pl": 0,
        ".": 0,
    }
    alphas = "abcdefghijklmnopqrstuvwxyz"

    n = 0
    out = []

    def consume_next_token():
        nonlocal n

        # Integers correspond to a single component.
        if v[n].isdigit():
            d = 0
            while n < len(v) and v[n].isdigit():
                d = (d * 10) + int(v[n])
                n += 1
            out.append(d)
            return

        # Modifiers correspond to a single component with a fixed value.
        for modifier, prio in modifiers.items():
            if v[n:].startswith(modifier):
                out.append(prio)
                n += len(modifier)
                return

        # Letters correspond to two components: (0, idx + 1).
        # For example: versions "0.2" and "0b" are identical.
        idx = alphas.find(v[n].lower())
        if idx >= 0:
            out.append(0)
            out.append(idx + 1)

        # Consume the character. It does _not_ contribute to the version.
        n += 1

    while n < len(v):
        consume_next_token()

    return out


def parse_version(v, *, strip_pkgname=True):
    if strip_pkgname:
        v = v.split("-")[-1]

    # Split into components + revision.
    # Technically, xbps's parsing code allows the revision to appear in the middle of a version.
    # We only accept revision at the end of a version string.
    parts = v.split("_")
    comps = parse_components(parts[0])
    if len(parts) > 1:
        if len(parts) != 2:
            raise RuntimeError("Expected at most one revision in xbps version")
        revision = int(parts[1])
    else:
        revision = 1

    return XbpsVersion(comps, revision)


def compare_version(v1, v2):
    for c1, c2 in itertools.zip_longest(v1.comps, v2.comps, fillvalue=0):
        if c1 != c2:
            return c1 - c2
    if v1.revision != v2.revision:
        return v1.revision - v2.revision
    return 0


# XBPS INSTALL/REMOVE scripts are called with arguments:
# <action> <pkgname> <version> <update yes/no> "no" <arch>


def compose_xbps_install(cfg, pkg):
    yml = pkg._this_yml.get("scripts", dict()).get("post_install")
    if not yml:
        return None

    step_sh = []
    for step_yml in yml:
        args_yml = step_yml["args"]
        if isinstance(args_yml, str):
            step_sh.append("(eval " + shlex.quote(args_yml) + ")")
        else:
            step_sh.append(
                "(exec `which "
                + shlex.quote(args_yml[0])
                + "`"
                + "".join(" " + shlex.quote(q) for q in args_yml[1:])
                + ")"
            )

    return (
        '#!/bin/sh\ncase "$1" in\npost)\n'
        + "".join(f"    {s}\n" for s in step_sh)
        + "    ;;\nesac\n"
    )
