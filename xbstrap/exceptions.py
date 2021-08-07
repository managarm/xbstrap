# SPDX-License-Identifier: MIT

# This module exists to solve circular dependencies between xbstrap.vcs_util
# and xbstrap.base; however, moving all exceptions here casues a new circular
# dependency: ExecutionFailureError needs Action.strings, defined in
# xbstrap.base, but xbstrap.base needs xbstrap.exceptions (this module).
# For this reason, further extraction has been halted, and the minimum (plus
# some more) was done to break the circular dependency.
# TODO(arsen): further disentangle exceptions from xbstrap.base


class GenericError(Exception):
    pass


class RollingIdUnavailableError(Exception):
    def __init__(self, name):
        super().__init__("No rolling_id specified for source {}".format(name))
