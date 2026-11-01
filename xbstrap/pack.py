# SPDX-License-Identifier: MIT

import os
import subprocess

import xbstrap.subpkgs as _subpkgs
import xbstrap.util as _util


# Stages the files of a package and calls xbps-create. The arguments are plain data since the
# caller may be a different process than the one that composed them.
def execute_pack(pack_args):
    root = pack_args["root"]
    os.makedirs(root, exist_ok=True)
    os.makedirs(pack_args["out_dir"], exist_ok=True)

    _subpkgs.install_entries(pack_args["entries"], pack_args["staging_dir"], root)

    if pack_args["install_sh"]:
        with open(os.path.join(root, "INSTALL"), "wt") as f:
            f.write(pack_args["install_sh"])

    _util.touchtree(root)

    # The directory is now prepared, call xbps-create.
    subprocess.check_call(
        pack_args["create_args"] + [root],
        env=_util.xbstrap_bin_environ(),
        cwd=pack_args["out_dir"],
        stdout=None if pack_args["verbose"] else subprocess.DEVNULL,
    )
