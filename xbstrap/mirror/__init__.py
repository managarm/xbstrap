#!/usr/bin/python3
# SPDX-License-Identifier: MIT

import argparse

import xbstrap.base

main_parser = argparse.ArgumentParser()
main_subcmds = main_parser.add_subparsers(metavar="<command>")
main_subcmds.required = True


def do_update(args):
    cfg = xbstrap.base.config_for_dir()
    plan = xbstrap.base.Plan(cfg)

    if args.dry_run:
        plan.dry_run = True
    if args.paranoid:
        plan.paranoid = True
    if args.keep_going:
        plan.keep_going = True

    # We always want to update mirrors.
    if not args.no_check:
        plan.check = True
    if not args.no_update:
        plan.update = True

    for src in cfg.all_sources():
        plan.wanted.add((xbstrap.base.Action.MIRROR_SRC, src))

    plan.run_plan()


update_parser = main_subcmds.add_parser("update")
update_parser.set_defaults(cmd=do_update)
update_parser.add_argument(
    "-n", "--dry-run", action="store_true", help="compute a plan but do not execute it"
)
update_parser.add_argument(
    "-C",
    "--no-check",
    action="store_true",
    help="do not skip packages that are already built/installed/etc.",
)
update_parser.add_argument(
    "-U", "--no-update", action="store_true", help="do not check for package updates"
)
update_parser.add_argument(
    "--paranoid",
    action="store_true",
    help="also consider unlikely updates (e.g., changes of git tags)",
)
update_parser.add_argument(
    "--keep-going",
    action="store_true",
    help="continue running even if some build steps fail",
)


def main():
    main_args = main_parser.parse_args()
    main_args.cmd(main_args)
