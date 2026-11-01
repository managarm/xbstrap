import os
import re
import shutil

import xbstrap.util as _util


class Mapping:
    __slots__ = ("dirent", "children", "claims")

    @staticmethod
    def discover(path):
        mapping = Mapping(None)
        Mapping._discover_children(path, "", mapping)
        return mapping

    @staticmethod
    def _discover_children(path, subdir, mapping):
        fullpath = os.path.join(path, subdir)
        for dirent in os.scandir(fullpath):
            child = Mapping(dirent)
            mapping.children[dirent.name] = child
            if child.directory:
                Mapping._discover_children(path, os.path.join(subdir, dirent.name), child)

    def __init__(self, dirent):
        if not dirent:
            directory = True
        else:
            directory = dirent.is_dir(follow_symlinks=False)
        self.dirent = dirent
        self.children = {} if directory else None
        self.claims = set()

    @property
    def directory(self):
        return self.children is not None

    def __repr__(self):
        def visit(mapping, path):
            line = f"{path}: {mapping.claims}"
            if mapping.directory:
                return ", ".join(
                    [line]
                    + [
                        visit(child, os.path.join(path, name))
                        for name, child in mapping.children.items()
                    ]
                )
            else:
                return line

        return visit(self, "/")


def determine_mapping(build):
    root = Mapping.discover(build.staging_dir)

    subpkg_to_patterns = {}
    for subpkg_name in build.all_subpkgs():
        subpkg = build.cfg.get_target_pkg(subpkg_name)
        if subpkg.is_main_pkg:
            continue
        subpkg_to_patterns[subpkg_name] = [
            re.compile(_util.translate_glob(incl, recursive=True, include_hidden=True))
            for incl in subpkg.subpkg_include
        ]

    def visit(mapping, path):
        if mapping.directory:
            for name, child in mapping.children.items():
                child_path = os.path.join(path, name)
                visit(child, child_path)
                mapping.claims.update(child.claims)
        else:
            for subpkg_name, patterns in subpkg_to_patterns.items():
                if any(pattern.match(path) for pattern in patterns):
                    mapping.claims.add(subpkg_name)
            if len(mapping.claims) > 1:
                raise RuntimeError(
                    f"File {path} is claimed by multiple subpackages: {mapping.claims}"
                )
            if not mapping.claims:
                mapping.claims.add(build.name)

    visit(root, "/")
    return root


# Returns (path, kind) for all entries claimed by the package, with parents before their children.
# Unlike a mapping, the result is plain data and can be passed to another process.
def list_mapping(pkg, root):
    entries = []

    def visit(mapping, path):
        if pkg.name not in mapping.claims:
            return

        if mapping.directory:
            # The caller of install_entries() creates the root directory.
            if mapping != root:
                entries.append((path, "dir"))

            for name, child in mapping.children.items():
                visit(child, os.path.join(path, name))
        elif mapping.dirent.is_symlink():
            entries.append((path, "symlink"))
        else:
            entries.append((path, "file"))

    visit(root, "")
    return entries


def install_entries(entries, src_dir, outdir):
    for path, kind in entries:
        src_path = os.path.join(src_dir, path)
        dest_path = os.path.join(outdir, path)

        if kind == "dir":
            os.mkdir(dest_path)
            shutil.copystat(src_path, dest_path)
        elif kind == "symlink":
            # Do not preserve attributes
            os.symlink(os.readlink(src_path), dest_path)
        else:
            shutil.copy2(src_path, dest_path)


# Yields (path, mapping) for all regular files in the mapping.
def walk_files(root):
    def visit(mapping, path):
        if mapping.directory:
            for name, child in mapping.children.items():
                yield from visit(child, os.path.join(path, name))
        elif mapping.dirent.is_file(follow_symlinks=False):
            yield path, mapping

    yield from visit(root, "/")
