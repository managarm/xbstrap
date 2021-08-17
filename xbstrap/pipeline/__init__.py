#!/usr/bin/python3
# SPDX-License-Identifier: MIT

import argparse
import json
import sys

import colorama
import yaml

import xbstrap.base
import xbstrap.cli_utils

main_parser = argparse.ArgumentParser()
main_parser.add_argument("-v", dest="verbose", action="store_true", help="verbose")
main_subparsers = main_parser.add_subparsers(dest="command")


class Pipeline:
    def __init__(self, cfg, pipe_yml):
        self.cfg = cfg
        self.jobs = dict()

        # Determine the set of jobs.
        mentioned_tools = set()
        mentioned_pkgs = set()
        for job_yml in pipe_yml["jobs"]:
            tools = []
            pkgs = []
            for name in job_yml.get("tools", []):
                tool = cfg.get_tool_pkg(name)
                tools.append(tool)
                mentioned_tools.add(tool)
            for name in job_yml.get("packages", []):
                pkg = cfg.get_target_pkg(name)
                pkgs.append(pkg)
                mentioned_pkgs.add(pkg)

            name = "batch:" + job_yml["name"]
            assert name not in self.jobs
            job = Job(name, tools, pkgs)
            if "capabilities" in job_yml:
                job.capabilities = set(job_yml["capabilities"])
            self.jobs[name] = job

            for name in job_yml.get("tasks", []):
                job.tasks.add(cfg.get_task(name))

        for tool in cfg.all_tools():
            if tool in mentioned_tools:
                continue
            if tool.stability_level == "broken":
                continue
            name = "tool:" + tool.name
            assert name not in self.jobs
            job = Job(name, [tool], [])
            if tool.stability_level == "unstable":
                job.unstable = True
            self.jobs[name] = job
        for pkg in cfg.all_pkgs():
            if pkg in mentioned_pkgs:
                continue
            if pkg.stability_level == "broken":
                continue
            name = "package:" + pkg.name
            assert name not in self.jobs
            job = Job(name, [], [pkg])
            if pkg.stability_level == "unstable":
                job.unstable = True
            self.jobs[name] = job

    def all_jobs(self):
        return self.jobs.values()

    def get_job(self, name):
        return self.jobs[name]


class Job:
    def __init__(self, name, tools, pkgs):
        self.name = name
        self.tools = set(tools)
        self.pkgs = set(pkgs)
        self.tasks = set()
        self.capabilities = set()
        self.unstable = False


def pipeline_for_dir(cfg):
    with open("pipeline.yml", "r") as f:
        pipe_yml = yaml.load(f, yaml.SafeLoader)
    return Pipeline(cfg, pipe_yml)


class PipelineItem:
    def __init__(self, job):
        self.job = job
        self.edge_set = set()
        self.edge_list = []
        self.plan_state = xbstrap.base.PlanState.NULL
        self.resolved_n = 0


def do_compute_graph(args):
    cfg = xbstrap.base.config_for_dir()
    pipe = pipeline_for_dir(cfg)

    if args.version_file:
        with xbstrap.cli_utils.open_file_from_cli(args.version_file, "rt") as f:
            version_yml = yaml.load(f, yaml.SafeLoader)
    if args.artifacts:
        out_root = dict()
        for job in pipe.all_jobs():
            up2date = False
            if args.version_file:
                up2date = True
                if len(job.tasks):  # For now, tasks are also always rebuilt.
                    up2date = False
                for tool in job.tools:
                    if tool.name not in version_yml["tools"]:
                        up2date = False
                        break
                    if tool.version != version_yml["tools"][tool.name]:
                        up2date = False
                        break
                for pkg in job.pkgs:
                    if pkg.name not in version_yml["pkgs"]:
                        up2date = False
                        break
                    if pkg.version != version_yml["pkgs"][pkg.name]:
                        up2date = False
                        break

            plan = xbstrap.base.Plan(cfg)
            plan.build_scope = set().union(job.tools, job.pkgs)
            for tool in job.tools:
                plan.wanted.update([(xbstrap.base.Action.ARCHIVE_TOOL, tool)])
            for pkg in job.pkgs:
                if cfg.use_xbps:
                    plan.wanted.update([(xbstrap.base.Action.PACK_PKG, pkg)])
                else:
                    plan.wanted.update([(xbstrap.base.Action.BUILD_PKG, pkg)])
            for task in job.tasks:
                plan.wanted.update([(xbstrap.base.Action.RUN, task)])
            plan.compute_plan(no_ordering=True)

            out_job = {
                "unstable": job.unstable,
                "up2date": up2date,
                "capabilities": list(job.capabilities),
            }
            out_job["products"] = {"tools": [], "pkgs": [], "files": []}
            out_job["needed"] = {"tools": [], "pkgs": []}
            for tool in job.tools:
                out_job["products"]["tools"].append(
                    {
                        "name": tool.name,
                        "version": tool.version,
                        "architecture": tool.architecture,
                    }
                )
            for pkg in job.pkgs:
                out_job["products"]["pkgs"].append(
                    {
                        "name": pkg.name,
                        "version": pkg.version,
                        "architecture": pkg.architecture,
                    }
                )
            for task in job.tasks:
                for af in task.artifact_files:
                    out_job["products"]["files"].append(
                        {
                            "name": af.name,
                            "filepath": af.filepath,
                            "architecture": af.architecture,
                        }
                    )
            for (action, subject) in plan.materialized_steps():
                if action == xbstrap.base.Action.WANT_TOOL:
                    if subject in job.tools:
                        continue
                    out_job["needed"]["tools"].append(
                        {
                            "name": subject.name,
                            "version": subject.version,
                            "architecture": subject.architecture,
                        }
                    )
                if action == xbstrap.base.Action.WANT_PKG:
                    if subject in job.pkgs:
                        continue
                    out_job["needed"]["pkgs"].append(
                        {
                            "name": subject.name,
                            "version": subject.version,
                            "architecture": subject.architecture,
                        }
                    )
            out_root[job.name] = out_job

        if args.json:
            print(json.dumps(out_root))
        else:
            print(yaml.dump(out_root), end="")
    else:
        items = dict()
        for job in pipe.all_jobs():
            item = PipelineItem(job)
            items[item.job.name] = item

        tool_mapping = dict()
        pkg_mapping = dict()
        for item in items.values():
            for tool in item.job.tools:
                tool_mapping[tool] = item.job.name
            for pkg in item.job.pkgs:
                pkg_mapping[pkg] = item.job.name

        for item in items.values():
            plan = xbstrap.base.Plan(cfg)
            plan.build_scope = set().union(item.job.tools, item.job.pkgs)
            for tool in item.job.tools:
                plan.wanted.update([(xbstrap.base.Action.ARCHIVE_TOOL, tool)])
            for pkg in item.job.pkgs:
                if cfg.use_xbps:
                    plan.wanted.update([(xbstrap.base.Action.PACK_PKG, pkg)])
                else:
                    plan.wanted.update([(xbstrap.base.Action.BUILD_PKG, pkg)])
            for task in item.job.tasks:
                plan.wanted.update([(xbstrap.base.Action.RUN, task)])
            plan.compute_plan(no_ordering=True)

            for (action, subject) in plan.materialized_steps():
                if action == xbstrap.base.Action.WANT_TOOL:
                    if subject in item.job.tools:
                        continue
                    item.edge_set.add(tool_mapping[subject])
                if action == xbstrap.base.Action.WANT_PKG:
                    if subject in item.job.pkgs:
                        continue
                    item.edge_set.add(pkg_mapping[subject])

        for item in items.values():
            item.edge_list = list(item.edge_set)

        order = []

        # TODO: this is copied from the planning code. Unify these code paths!
        # The following code does a topologic sort of the desired items.
        stack = []

        def visit(item):
            if item.plan_state == xbstrap.base.PlanState.NULL:
                item.plan_state = xbstrap.base.PlanState.EXPANDING
                stack.append(item)
            elif item.plan_state == xbstrap.base.PlanState.EXPANDING:
                reverse_chain = [item]
                for circ_item in reversed(stack):
                    reverse_chain.append(circ_item)
                    if circ_item == item:
                        break
                chain = reversed(reverse_chain)
                raise RuntimeError(
                    "Job has circular dependencies {}".format(
                        [chain_item.job.name for chain_item in chain]
                    )
                )
            else:
                # Packages that are already ordered do not need to be considered again.
                assert item.plan_state == xbstrap.base.PlanState.ORDERED

        for root_item in items.values():
            visit(root_item)

            while stack:
                item = stack[-1]
                if item.resolved_n == len(item.edge_list):
                    assert item.plan_state == xbstrap.base.PlanState.EXPANDING
                    item.plan_state = xbstrap.base.PlanState.ORDERED
                    stack.pop()
                    order.append(item)
                else:
                    edge_item = items[item.edge_list[item.resolved_n]]
                    item.resolved_n += 1
                    visit(edge_item)

        if args.gv:
            # For visualization purposes.
            print("digraph {")
            for item in order:
                for edge in item.edge_list:
                    print('    "{}" -> "{}";'.format(edge, item.job.name))
            print("}")
        elif args.linear:
            for item in order:
                print("{}".format(item.job.name))
        else:
            for item in order:
                print("{} {}".format(item.job.name, " ".join(item.edge_list)))


do_compute_graph.parser = main_subparsers.add_parser("compute-graph")
do_compute_graph.parser.add_argument("--artifacts", action="store_true")
do_compute_graph.parser.add_argument("--linear", action="store_true")
do_compute_graph.parser.add_argument("--gv", action="store_true")
do_compute_graph.parser.add_argument("--json", action="store_true")
do_compute_graph.parser.add_argument(
    "--version-file", type=str, help="file that reports existing file versions"
)


def do_run_job(args):
    cfg = xbstrap.base.config_for_dir()
    pipe = pipeline_for_dir(cfg)
    job = pipe.get_job(args.job)

    plan = xbstrap.base.Plan(cfg)
    if args.dry_run:
        plan.dry_run = True
    if args.check:
        plan.check = True
    if args.keep_going:
        plan.keep_going = True
    plan.build_scope = set().union(job.tools, job.pkgs)

    if args.progress_file is not None:
        plan.progress_file = xbstrap.cli_utils.open_file_from_cli(args.progress_file, "wt")

    for tool in job.tools:
        plan.wanted.update([(xbstrap.base.Action.ARCHIVE_TOOL, tool)])
    for pkg in job.pkgs:
        if cfg.use_xbps:
            plan.wanted.update([(xbstrap.base.Action.PACK_PKG, pkg)])
        else:
            plan.wanted.update([(xbstrap.base.Action.BUILD_PKG, pkg)])
    for task in job.tasks:
        plan.wanted.update([(xbstrap.base.Action.RUN, task)])
    plan.run_plan()


do_run_job.parser = main_subparsers.add_parser("run-job")
do_run_job.parser.add_argument("job", type=str)
do_run_job.parser.add_argument(
    "-n", "--dry-run", action="store_true", help="compute a plan but do not execute it"
)
do_run_job.parser.add_argument(
    "-c",
    "--check",
    action="store_true",
    help="skip packages that are already built/installed/etc.",
)
do_run_job.parser.add_argument(
    "--keep-going",
    action="store_true",
    help="continue running even if some build steps fail",
)
do_run_job.parser.add_argument(
    "--progress-file",
    type=str,
    help="file that receives machine-ready progress notifications",
)


def main():
    args = main_parser.parse_args()

    colorama.init()

    if args.verbose:
        xbstrap.base.verbosity = True

    try:
        if args.command == "compute-graph":
            do_compute_graph(args)
        elif args.command == "run-job":
            do_run_job(args)
        else:
            assert not "Unexpected command"
    except (
        xbstrap.base.ExecutionFailureError,
        xbstrap.base.PlanFailureError,
    ) as e:
        print(
            "{}xbstrap{}: {}{}{}".format(
                colorama.Style.BRIGHT,
                colorama.Style.RESET_ALL,
                colorama.Fore.RED,
                e,
                colorama.Style.RESET_ALL,
            )
        )
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)
