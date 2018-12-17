# xbstrap: Build system for OS distributions

xbstrap is a build system designed to build "distributions" consisting of multiple (usually many) packages.
It does not replace neither `make` and `ninja` nor `autoconf`, `automake`, `meson` or `cmake` and similar utilities.
Instead, xbstrap is intended to invoke those build systems in the correct order, while respecting inter-package dependencies.
