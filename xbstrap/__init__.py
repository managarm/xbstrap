# SPDX-License-Identifier: MIT

import argparse
import json
import os
import shutil
import sys
import tarfile
import urllib.parse

import colorama
import yaml

import xbstrap.base
import xbstrap.cli_utils
import xbstrap.exceptions
import xbstrap.util

# ---------------------------------------------------------------------------------------
# Command line parsing.
# ---------------------------------------------------------------------------------------

main_parser = argparse.ArgumentParser()
main_parser.add_argument("-v", dest="verbose", action="store_true", help="verbose")
main_subparsers = main_parser.add_subparsers(dest="command")


def do_runtool(args):
    cfg = xbstrap.base.config_for_dir()

    tool_pkgs = []
    workdir = None
    for_package = False

    if args.build is not None:
        pkg = cfg.get_target_pkg(args.build)

        workdir = pkg.build_dir
        tool_pkgs.extend(cfg.get_tool_pkg(name) for name in pkg.tool_dependencies)
        args = args.opts
        for_package = True
    else:
        if "--" not in args.opts:
            main_parser.error("tools and arguments must be separated by --")

        d = args.opts.index("--")
        tools = args.opts[:d]
        args = args.opts[(d + 1) :]

        if not args:
            main_parser.error("no command given")

        for name in tools:
            tool_pkgs.append(cfg.get_tool_pkg(name))

    xbstrap.base.run_program(
        cfg,
        None,
        None,
        args,
        tool_pkgs=tool_pkgs,
        workdir=workdir,
        for_package=for_package,
    )


do_runtool.parser = main_subparsers.add_parser("runtool")
do_runtool.parser.add_argument("--build", type=str)
do_runtool.parser.add_argument("opts", nargs=argparse.REMAINDER)


def do_init(args):
    if not os.access(os.path.join(args.src_root, "bootstrap.yml"), os.F_OK):
        raise RuntimeError("Given src_root does not contain a bootstrap.yml")
    elif os.path.exists("bootstrap.link"):
        print("warning: bootstrap.link already exists, skipping...")
    else:
        os.symlink(os.path.join(args.src_root, "bootstrap.yml"), "bootstrap.link")

    cfg = xbstrap.base.config_for_dir()
    if cfg.cargo_config_toml is not None:
        print("Creating cargo-home/config.toml")
        os.makedirs("cargo-home", exist_ok=True)
        shutil.copy(os.path.join(args.src_root, cfg.cargo_config_toml), "cargo-home/config.toml")

        container = cfg._site_yml.get("container", dict())
        if "build_mount" in container:
            build_root = container["build_mount"]
            source_root = container["src_mount"]
        else:
            print("Using non-Docker build")
            build_root = os.getcwd()
            source_root = os.path.abspath(args.src_root)

        with open("cargo-home/config.toml", "r") as f:

            def substitute(varname):
                if varname == "SOURCE_ROOT":
                    return source_root
                elif varname == "BUILD_ROOT":
                    return build_root

            content = xbstrap.base.replace_at_vars(f.read(), substitute)

        with open("cargo-home/config.toml", "w") as f:
            f.write(content)


do_init.parser = main_subparsers.add_parser("init")
do_init.parser.add_argument("src_root", type=str)


def handle_plan_args(cfg, plan, args):
    if args.dry_run:
        plan.dry_run = True
    if args.check:
        plan.check = True
    if args.update:
        plan.update = True
    if args.recursive:
        plan.recursive = True
    if args.paranoid:
        plan.paranoid = True
    if args.reset:
        plan.reset = xbstrap.base.ResetMode.RESET
    if args.hard_reset:
        plan.reset = xbstrap.base.ResetMode.HARD_RESET
    if args.only_wanted:
        plan.only_wanted = True
    if args.keep_going:
        plan.keep_going = True

    if args.progress_file is not None:
        plan.progress_file = xbstrap.cli_utils.open_file_from_cli(args.progress_file, "wt")


handle_plan_args.parser = argparse.ArgumentParser(add_help=False)
handle_plan_args.parser.add_argument(
    "-n", "--dry-run", action="store_true", help="compute a plan but do not execute it"
)
handle_plan_args.parser.add_argument(
    "-c",
    "--check",
    action="store_true",
    help="skip packages that are already built/installed/etc.",
)
handle_plan_args.parser.add_argument(
    "-u", "--update", action="store_true", help="check for package updates"
)
handle_plan_args.parser.add_argument(
    "--recursive", action="store_true", help="when updating: also update requirements"
)
handle_plan_args.parser.add_argument(
    "--paranoid",
    action="store_true",
    help="also consider unlikely updates (e.g., changes of git tags)",
)
handle_plan_args.parser.add_argument(
    "--reset",
    action="store_true",
    help="reset repository state; risks loss of local commits!",
)
handle_plan_args.parser.add_argument(
    "--hard-reset",
    action="store_true",
    help="clean and reset repository state; risks loss of local changes and commits!",
)
handle_plan_args.parser.add_argument(
    "--only-wanted",
    action="store_true",
    help="fail steps that are not explicitly wanted",
)
handle_plan_args.parser.add_argument(
    "--keep-going",
    action="store_true",
    help="continue running even if some build steps fail",
)
handle_plan_args.parser.add_argument(
    "--progress-file",
    type=str,
    help="file that receives machine-ready progress notifications",
)


def do_list_srcs(args):
    cfg = xbstrap.base.config_for_dir()
    for src in cfg.all_sources():
        print("Source: {}".format(src.name))


do_list_srcs.parser = main_subparsers.add_parser("list-srcs")


def do_fetch(args):
    cfg = xbstrap.base.config_for_dir()
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)

    if args.all:
        for src in cfg.all_sources():
            print("Fetching  {}".format(src.name))
            plan.wanted.add((xbstrap.base.Action.FETCH_SRC, src))
    else:
        for src_name in args.source:
            src = cfg.get_source(src_name)
            plan.wanted.add((xbstrap.base.Action.FETCH_SRC, src))

    plan.run_plan()


do_fetch.parser = main_subparsers.add_parser("fetch", parents=[handle_plan_args.parser])
do_fetch.parser.add_argument("--all", action="store_true")
do_fetch.parser.add_argument("source", nargs="*", type=str)


def do_checkout(args):
    cfg = xbstrap.base.config_for_dir()
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)

    if args.all:
        for src in cfg.all_sources():
            print("Checking Out  {}".format(src.name))
            plan.wanted.add((xbstrap.base.Action.CHECKOUT_SRC, src))
    else:
        for src_name in args.source:
            src = cfg.get_source(src_name)
            plan.wanted.add((xbstrap.base.Action.CHECKOUT_SRC, src))

    plan.run_plan()


do_checkout.parser = main_subparsers.add_parser("checkout", parents=[handle_plan_args.parser])
do_checkout.parser.add_argument("--all", action="store_true")
do_checkout.parser.add_argument("source", nargs="*", type=str)


def do_patch(args):
    cfg = xbstrap.base.config_for_dir()
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)

    if args.all:
        for src in cfg.all_sources():
            print("Patching  {}".format(src.name))
            plan.wanted.add((xbstrap.base.Action.PATCH_SRC, src))
    else:
        for src_name in args.source:
            src = cfg.get_source(src_name)
            plan.wanted.add((xbstrap.base.Action.PATCH_SRC, src))

    plan.run_plan()


do_patch.parser = main_subparsers.add_parser("patch", parents=[handle_plan_args.parser])
do_patch.parser.add_argument("--all", action="store_true")
do_patch.parser.add_argument("source", nargs="*", type=str)


def do_regenerate(args):
    cfg = xbstrap.base.config_for_dir()
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)

    if args.all:
        for src in cfg.all_sources():
            print("Regenerating  {}".format(src.name))
            plan.wanted.add((xbstrap.base.Action.REGENERATE_SRC, src))
    else:
        for src_name in args.source:
            src = cfg.get_source(src_name)
            plan.wanted.add((xbstrap.base.Action.REGENERATE_SRC, src))

    plan.run_plan()


do_regenerate.parser = main_subparsers.add_parser("regenerate", parents=[handle_plan_args.parser])
do_regenerate.parser.add_argument("--all", action="store_true")
do_regenerate.parser.add_argument("source", nargs="*", type=str)


def select_tools(cfg, args):
    if args.all:
        return [tool for tool in cfg.all_tools() if tool.is_default]
    else:
        sel = [cfg.get_tool_pkg(name) for name in args.tools]

        if args.build_deps_of is not None:
            for pkg_name in args.build_deps_of:
                pkg = cfg.get_target_pkg(pkg_name)
                for tool in pkg.tool_dependencies:
                    sel.append(cfg.get_tool_pkg(tool))

        # Deduplicate sel
        sel = list(dict.fromkeys(sel))

        return sel


select_tools.parser = argparse.ArgumentParser(add_help=False)
select_tools.parser.add_argument("--all", action="store_true")
select_tools.parser.add_argument("--build-deps-of", type=str, action="append")
select_tools.parser.add_argument("tools", nargs="*", type=str)


def reconfigure_and_recompile_tools(plan, args, sel):
    if args.reconfigure:
        for tool in sel:
            plan.wanted.add((xbstrap.base.Action.CONFIGURE_TOOL, tool))
            for stage in tool.all_stages():
                plan.wanted.add((xbstrap.base.Action.COMPILE_TOOL_STAGE, stage))
    elif args.recompile:
        for tool in sel:
            for stage in tool.all_stages():
                plan.wanted.add((xbstrap.base.Action.COMPILE_TOOL_STAGE, stage))


reconfigure_tools_parser = argparse.ArgumentParser(add_help=False)
reconfigure_tools_parser.add_argument("--reconfigure", action="store_true")
reconfigure_tools_parser.set_defaults(reconfigure=False, recompile=False)

recompile_tools_parser = argparse.ArgumentParser(add_help=False)
recompile_tools_parser.add_argument("--recompile", action="store_true")
recompile_tools_parser.set_defaults(reconfigure=False, recompile=False)


def do_configure_tool(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_tools(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    plan.wanted.update([(xbstrap.base.Action.CONFIGURE_TOOL, pkg) for pkg in sel])
    plan.run_plan()


do_configure_tool.parser = main_subparsers.add_parser(
    "configure-tool", parents=[handle_plan_args.parser, select_tools.parser]
)


def do_compile_tool(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_tools(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    reconfigure_and_recompile_tools(plan, args, sel)
    plan.wanted.update(
        [
            (xbstrap.base.Action.COMPILE_TOOL_STAGE, stage)
            for pkg in sel
            for stage in pkg.all_stages()
        ]
    )
    plan.run_plan()


do_compile_tool.parser = main_subparsers.add_parser(
    "compile-tool",
    parents=[handle_plan_args.parser, select_tools.parser, reconfigure_tools_parser],
)


def do_install_tool(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_tools(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    reconfigure_and_recompile_tools(plan, args, sel)
    plan.wanted.update(
        [
            (xbstrap.base.Action.INSTALL_TOOL_STAGE, stage)
            for pkg in sel
            for stage in pkg.all_stages()
        ]
    )
    plan.run_plan()


do_install_tool.parser = main_subparsers.add_parser(
    "install-tool",
    parents=[
        handle_plan_args.parser,
        select_tools.parser,
        reconfigure_tools_parser,
        recompile_tools_parser,
    ],
)


def select_pkgs(cfg, args):
    if args.all:
        return [pkg for pkg in cfg.all_pkgs() if pkg.is_default]
    else:
        if args.command == "run":
            return [cfg.get_target_pkg(name) for name in args.pkg]
        else:
            sel = [cfg.get_target_pkg(name) for name in args.packages]

            if args.deps_of is not None:
                for pkg_name in args.deps_of:
                    pkg = cfg.get_target_pkg(pkg_name)
                    sel.append(pkg)
                    for dep_name in pkg.discover_recursive_pkg_dependencies():
                        dep = cfg.get_target_pkg(dep_name)
                        sel.append(dep)

            return sel


select_pkgs.parser = argparse.ArgumentParser(add_help=False)
select_pkgs.parser.add_argument("--all", action="store_true")
select_pkgs.parser.add_argument("--deps-of", type=str, action="append")
select_pkgs.parser.add_argument("packages", nargs="*", type=str)


def reconfigure_and_rebuild_pkgs(plan, args, sel, no_pack=False):
    if args.reconfigure:
        for pkg in sel:
            plan.wanted.add((xbstrap.base.Action.CONFIGURE_PKG, pkg))
            plan.wanted.add((xbstrap.base.Action.BUILD_PKG, pkg))
            if no_pack:
                return
            if plan.cfg.use_xbps:
                plan.wanted.add((xbstrap.base.Action.PACK_PKG, pkg))
    elif args.rebuild:
        for pkg in sel:
            plan.wanted.add((xbstrap.base.Action.BUILD_PKG, pkg))
            if no_pack:
                return
            if plan.cfg.use_xbps:
                plan.wanted.add((xbstrap.base.Action.PACK_PKG, pkg))


reconfigure_pkgs_parser = argparse.ArgumentParser(add_help=False)
reconfigure_pkgs_parser.add_argument("--reconfigure", action="store_true")
reconfigure_pkgs_parser.set_defaults(reconfigure=False, rebuild=False)

rebuild_pkgs_parser = argparse.ArgumentParser(add_help=False)
rebuild_pkgs_parser.add_argument("--rebuild", action="store_true")
rebuild_pkgs_parser.set_defaults(reconfigure=False, rebuild=False)


def do_configure(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_pkgs(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    plan.wanted.update([(xbstrap.base.Action.CONFIGURE_PKG, pkg) for pkg in sel])
    plan.run_plan()


do_configure.parser = main_subparsers.add_parser(
    "configure", parents=[handle_plan_args.parser, select_pkgs.parser]
)


def do_build(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_pkgs(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    reconfigure_and_rebuild_pkgs(plan, args, sel, no_pack=True)
    plan.wanted.update([(xbstrap.base.Action.BUILD_PKG, pkg) for pkg in sel])
    plan.run_plan()


do_build.parser = main_subparsers.add_parser(
    "build",
    parents=[handle_plan_args.parser, reconfigure_pkgs_parser, select_pkgs.parser],
)


def do_reproduce_build(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_pkgs(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    reconfigure_and_rebuild_pkgs(plan, args, sel, no_pack=True)
    plan.wanted.update([(xbstrap.base.Action.REPRODUCE_BUILD_PKG, pkg) for pkg in sel])
    plan.run_plan()


do_reproduce_build.parser = main_subparsers.add_parser(
    "reproduce-build",
    parents=[handle_plan_args.parser, reconfigure_pkgs_parser, select_pkgs.parser],
)


def do_pack(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_pkgs(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    reconfigure_and_rebuild_pkgs(plan, args, sel, no_pack=True)
    plan.wanted.update([(xbstrap.base.Action.PACK_PKG, pkg) for pkg in sel])
    plan.run_plan()


do_pack.parser = main_subparsers.add_parser(
    "pack",
    parents=[handle_plan_args.parser, reconfigure_pkgs_parser, select_pkgs.parser],
)


def do_reproduce_pack(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_pkgs(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    reconfigure_and_rebuild_pkgs(plan, args, sel, no_pack=True)
    plan.wanted.update([(xbstrap.base.Action.REPRODUCE_PACK_PKG, pkg) for pkg in sel])
    plan.run_plan()


do_reproduce_pack.parser = main_subparsers.add_parser(
    "reproduce-pack",
    parents=[handle_plan_args.parser, reconfigure_pkgs_parser, select_pkgs.parser],
)


def do_download(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_pkgs(cfg, args)

    if cfg.pkg_archives_url is None:
        raise RuntimeError("No repository URL in bootstrap.yml")

    xbstrap.util.try_mkdir(cfg.package_out_dir)

    for pkg in sel:
        url = urllib.parse.urljoin(cfg.pkg_archives_url + "/", pkg.name + ".tar.gz")
        print(
            "{}xbstrap{}: Downloading package {} from {}".format(
                colorama.Style.BRIGHT, colorama.Style.RESET_ALL, pkg.name, url
            )
        )
        xbstrap.util.interactive_download(url, pkg.archive_file)

        xbstrap.base.try_rmtree(pkg.staging_dir)
        os.mkdir(pkg.staging_dir)
        with tarfile.open(pkg.archive_file, "r:gz") as tar:
            for info in tar:
                tar.extract(info, pkg.staging_dir)


do_download.parser = main_subparsers.add_parser("download-archive", parents=[select_pkgs.parser])


def do_download_tool(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_tools(cfg, args)

    if len(sel) == 0:
        print(
            "{}xbstrap{}: No tools to download".format(
                colorama.Style.BRIGHT, colorama.Style.RESET_ALL
            )
        )
        return

    if args.dry_run:
        for tool in sel:
            url = urllib.parse.urljoin(cfg.tool_archives_url + "/", tool.name + ".tar.gz")
            print(
                "{}xbstrap{}: Will download tool {} from {}".format(
                    colorama.Style.BRIGHT, colorama.Style.RESET_ALL, tool.name, url
                )
            )
        return

    if cfg.tool_archives_url is None:
        raise RuntimeError("No repository URL in bootstrap.yml")

    xbstrap.util.try_mkdir(cfg.tool_out_dir)

    for tool in sel:
        url = urllib.parse.urljoin(cfg.tool_archives_url + "/", tool.name + ".tar.gz")
        print(
            "{}xbstrap{}: Downloading tool {} from {}".format(
                colorama.Style.BRIGHT, colorama.Style.RESET_ALL, tool.name, url
            )
        )
        xbstrap.util.interactive_download(url, tool.archive_file)

        xbstrap.base.try_rmtree(tool.prefix_dir)
        os.mkdir(tool.prefix_dir)
        with tarfile.open(tool.archive_file, "r:gz") as tar:
            for info in tar:
                tar.extract(info, tool.prefix_dir)


do_download_tool.parser = main_subparsers.add_parser(
    "download-tool-archive", parents=[select_tools.parser]
)
do_download_tool.parser.add_argument(
    "-n",
    "--dry-run",
    action="store_true",
    help="show which tools will be installed but don't download anything",
)
do_download_tool.parser.set_defaults(_impl=do_download_tool)


def do_install(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_pkgs(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    reconfigure_and_rebuild_pkgs(plan, args, sel)
    plan.wanted.update([(xbstrap.base.Action.INSTALL_PKG, pkg) for pkg in sel])
    plan.run_plan()


do_install.parser = main_subparsers.add_parser(
    "install",
    parents=[
        handle_plan_args.parser,
        reconfigure_pkgs_parser,
        rebuild_pkgs_parser,
        select_pkgs.parser,
    ],
)


def do_archive_tool(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_tools(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    plan.wanted.update([(xbstrap.base.Action.ARCHIVE_TOOL, tool) for tool in sel])
    plan.run_plan()


do_archive_tool.parser = main_subparsers.add_parser(
    "archive-tool", parents=[handle_plan_args.parser, select_tools.parser]
)


def do_archive(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_pkgs(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    plan.wanted.update([(xbstrap.base.Action.ARCHIVE_PKG, pkg) for pkg in sel])
    plan.run_plan()


do_archive.parser = main_subparsers.add_parser(
    "archive", parents=[handle_plan_args.parser, select_pkgs.parser]
)

# ----------------------------------------------------------------------------------------


def do_pull_pack(args):
    cfg = xbstrap.base.config_for_dir()
    sel = select_pkgs(cfg, args)
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)
    plan.wanted.update([(xbstrap.base.Action.PULL_PKG_PACK, pkg) for pkg in sel])
    plan.run_plan()


pull_pack_parser = main_subparsers.add_parser(
    "pull-pack", parents=[handle_plan_args.parser, select_pkgs.parser]
)
pull_pack_parser.set_defaults(_impl=do_pull_pack)

# ----------------------------------------------------------------------------------------


def do_list_tools(args):
    cfg = xbstrap.base.config_for_dir()
    for tool in cfg.all_tools():
        print(tool.name)


do_list_tools.parser = main_subparsers.add_parser("list-tools")


def do_list_pkgs(args):
    cfg = xbstrap.base.config_for_dir()
    for tool in cfg.all_pkgs():
        print(tool.name)


do_list_pkgs.parser = main_subparsers.add_parser("list-pkgs")


def do_run_task(args):
    args.all = False

    cfg = xbstrap.base.config_for_dir()
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)

    if args.pkg:
        sel = select_pkgs(cfg, args)
        for task_name in args.task:
            task = sel[0].get_task(task_name)
            if not task:
                raise RuntimeError(
                    "task {} of package {} not found".format(args.task[0], task_name)
                )
            plan.wanted.add((xbstrap.base.Action.RUN_PKG, task))
    elif args.tool:
        args.tools = args.tool
        sel = select_tools(cfg, args)
        for task_name in args.task:
            task = sel[0].get_task(task_name)
            if not task:
                raise RuntimeError("task {} of tool {} not found".format(args.task[0], task_name))
            plan.wanted.add((xbstrap.base.Action.RUN_TOOL, task))
    else:
        for task_name in args.task:
            task = cfg.get_task(task_name)
            if not task:
                raise RuntimeError("task {} not found".format(task_name))
            plan.wanted.add((xbstrap.base.Action.RUN, task))

    plan.run_plan()


do_run_task.parser = main_subparsers.add_parser("run", parents=[handle_plan_args.parser])
group = do_run_task.parser.add_mutually_exclusive_group(required=False)
group.add_argument("--pkg", nargs=1, required=False, type=str)
group.add_argument("--tool", nargs=1, required=False, type=str)
do_run_task.parser.add_argument("task", nargs="+", type=str)

# ----------------------------------------------------------------------------------------

var_commits_parser = main_subparsers.add_parser("variable-commits")
var_commits_subparsers = var_commits_parser.add_subparsers(dest="command")


def do_var_commits_fetch(args):
    cfg = xbstrap.base.config_for_dir()
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)

    for src in cfg.all_sources():
        if not src.has_variable_checkout_commit:
            continue
        plan.wanted.add((xbstrap.base.Action.FETCH_SRC, src))

    plan.run_plan()


do_var_commits_fetch.parser = var_commits_subparsers.add_parser(
    "fetch", parents=[handle_plan_args.parser]
)
do_var_commits_fetch.parser.set_defaults(_impl=do_var_commits_fetch)


def do_var_commits_determine(args):
    cfg = xbstrap.base.config_for_dir()

    out_yml = dict()
    for src in cfg.all_sources():
        if not src.has_variable_checkout_commit:
            continue
        out_yml[src.name] = src.determine_variable_checkout_commit()

    if args.json:
        json.dump(out_yml, sys.stdout)
    else:
        print(yaml.dump(out_yml), end="")


var_commits_determine_parser = var_commits_subparsers.add_parser("determine")
var_commits_determine_parser.set_defaults(_impl=do_var_commits_determine)
var_commits_determine_parser.add_argument("--json", action="store_true")

# ----------------------------------------------------------------------------------------

rolling_parser = main_subparsers.add_parser("rolling-versions")
rolling_subparsers = rolling_parser.add_subparsers(dest="command")


def do_rolling_fetch(args):
    cfg = xbstrap.base.config_for_dir()
    plan = xbstrap.base.Plan(cfg)
    handle_plan_args(cfg, plan, args)

    for src in cfg.all_sources():
        if not src.is_rolling_version:
            continue
        plan.wanted.add((xbstrap.base.Action.FETCH_SRC, src))

    plan.run_plan()


do_rolling_fetch.parser = rolling_subparsers.add_parser("fetch", parents=[handle_plan_args.parser])
do_rolling_fetch.parser.set_defaults(_impl=do_rolling_fetch)


def do_rolling_determine(args):
    cfg = xbstrap.base.config_for_dir()
    out_yml = dict()
    for src in cfg.all_sources():
        if not src.is_rolling_version:
            continue
        out_yml[src.name] = src.determine_rolling_id()

    if args.json:
        json.dump(out_yml, sys.stdout)
    else:
        print(yaml.dump(out_yml), end="")


do_rolling_determine.parser = rolling_subparsers.add_parser("determine")
do_rolling_determine.parser.add_argument("--json", action="store_true")
do_rolling_determine.parser.set_defaults(_impl=do_rolling_determine)

# ----------------------------------------------------------------------------------------


def do_prereqs(args):
    comps = set(args.components)
    valid_comps = ["cbuildrt", "xbps"]
    if not comps.issubset(valid_comps):
        raise RuntimeError(f"Unknown component given; choose from: {valid_comps}")

    home = xbstrap.util.find_home()
    bin_dir = os.path.join(home, "bin")
    xbstrap.util.try_mkdir(home)
    xbstrap.util.try_mkdir(bin_dir)

    if "cbuildrt" in comps:
        url = "https://github.com/managarm/cbuildrt"
        url += "/releases/latest/download/cbuildrt-linux-x86_64-static.tar"
        tar_path = os.path.join(home, "cbuildrt.tar")

        print(f"Downloading cbuildrt from {url}")
        xbstrap.util.interactive_download(url, tar_path)
        with tarfile.open(tar_path, "r") as tar:
            for info in tar:
                if info.name == "cbuildrt":
                    tar.extract(info, bin_dir)
        os.chmod(os.path.join(bin_dir, "cbuildrt"), 0o755)
    if "xbps" in comps:
        url = "https://alpha.de.repo.voidlinux.org/static"
        url += "/xbps-static-static-0.59_5.x86_64-musl.tar.xz"
        tar_path = os.path.join(home, "xbps.tar.xz")

        print(f"Downloading xbps from {url}")
        xbstrap.util.interactive_download(url, tar_path)
        with tarfile.open(tar_path, "r:xz") as tar:
            for info in tar:
                if os.path.dirname(info.name) == "./usr/bin":
                    info.name = os.path.basename(info.name)
                    tar.extract(info, bin_dir)


do_prereqs.parser = main_subparsers.add_parser("prereqs")
do_prereqs.parser.add_argument("components", type=str, nargs="*")
do_prereqs.parser.set_defaults(_impl=do_prereqs)

# ----------------------------------------------------------------------------------------


def do_execute_manifest(args):
    if args.c is not None:
        manifest = yaml.load(args.c, Loader=xbstrap.base.global_yaml_loader)
    else:
        manifest = yaml.load(sys.stdin, Loader=xbstrap.base.global_yaml_loader)
    xbstrap.base.execute_manifest(manifest)


execute_manifest_parser = main_subparsers.add_parser("execute-manifest")
execute_manifest_parser.add_argument("-c", type=str)
execute_manifest_parser.set_defaults(_impl=do_execute_manifest)


def main():
    args = main_parser.parse_args()

    colorama.init()

    if args.verbose:
        xbstrap.base.verbosity = True

    if not xbstrap.base.native_yaml_available:
        print(
            "{}xbstrap{}: {}Using pure Python YAML parser\n"
            "       : Install libyaml for improved performance{}".format(
                colorama.Style.BRIGHT,
                colorama.Style.RESET_ALL,
                colorama.Fore.YELLOW,
                colorama.Style.RESET_ALL,
            )
        )

    try:
        if hasattr(args, "_impl"):
            args._impl(args)
        elif args.command == "init":
            do_init(args)
        elif args.command == "runtool":
            do_runtool(args)
        elif args.command == "fetch":
            do_fetch(args)
        elif args.command == "checkout":
            do_checkout(args)
        elif args.command == "patch":
            do_patch(args)
        elif args.command == "regenerate":
            do_regenerate(args)
        elif args.command == "configure-tool":
            do_configure_tool(args)
        elif args.command == "compile-tool":
            do_compile_tool(args)
        elif args.command == "install-tool":
            do_install_tool(args)
        elif args.command == "configure":
            do_configure(args)
        elif args.command == "build":
            do_build(args)
        elif args.command == "reproduce-build":
            do_reproduce_build(args)
        elif args.command == "pack":
            do_pack(args)
        elif args.command == "reproduce-pack":
            do_reproduce_pack(args)
        elif args.command == "archive-tool":
            do_archive_tool(args)
        elif args.command == "archive":
            do_archive(args)
        elif args.command == "download":
            do_download(args)
        elif args.command == "install":
            do_install(args)
        elif args.command == "list-tools":
            do_list_tools(args)
        elif args.command == "list-pkgs":
            do_list_pkgs(args)
        elif args.command == "list-srcs":
            do_list_srcs(args)
        elif args.command == "run":
            do_run_task(args)
        else:
            assert not "Unexpected command"
    except (
        xbstrap.base.ExecutionFailureError,
        xbstrap.base.PlanFailureError,
        xbstrap.exceptions.GenericError,
    ) as e:
        xbstrap.util.log_err(e)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)
