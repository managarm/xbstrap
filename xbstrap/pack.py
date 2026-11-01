# SPDX-License-Identifier: MIT

import os
import shutil
import stat
import subprocess
import tempfile

import xbstrap.subpkgs as _subpkgs
import xbstrap.util as _util


# Copies the binary package out of a temporary volume, which goes away when this process exits.
# This gives up root first since the copy has to be owned by the host user.
def publish_package(path, publish):
    with open(path, "rb") as src:
        os.setgid(publish["gid"])
        os.setuid(publish["uid"])

        dest_dir = publish["dest_dir"]
        fd, tmp_path = tempfile.mkstemp(dir=dest_dir, prefix=publish["file"] + ".")
        try:
            with os.fdopen(fd, "wb") as dst:
                shutil.copyfileobj(src, dst)
                os.fchmod(dst.fileno(), stat.S_IMODE(os.fstat(src.fileno()).st_mode))
            os.replace(tmp_path, os.path.join(dest_dir, publish["file"]))
        except BaseException:
            os.unlink(tmp_path)
            raise


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

    if "publish" in pack_args:
        publish_package(
            os.path.join(pack_args["out_dir"], pack_args["publish"]["file"]), pack_args["publish"]
        )
