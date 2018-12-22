# xbstrap: Build system for OS distributions

xbstrap is a build system designed to build "distributions" consisting of multiple (usually many) packages.
It does not replace neither `make` and `ninja` nor `autoconf`, `automake`, `meson` or `cmake` and similar utilities.
Instead, xbstrap is intended to invoke those build systems in the correct order, while respecting inter-package dependencies.

## Installation

xbstrap is available from PyPI. To install it using pip, use:
```
pip3 install xbstrap
```

## Basic usage

See the [boostrap-managarm repository](https://github.com/managarm/bootstrap-managarm) for an example `bootstrap.yml` file.

Installing all tools (that run on the build system) is done using:
```
xbstrap install-tool --all
```
Installing all packages to a sysroot (of the host system):
```
xbstrap install --all
```
It is often useful to rebuild specific packages. Rebuilding package `foobar` can be done by:
```
xbstrap install --rebuild foobar
```
If the `configure` script shall be run again, use instead:
```
xbstrap install --reconfigure foobar
```
