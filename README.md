# xbstrap: Build system for OS distributions

xbstrap is a build system designed to build "distributions" consisting of multiple (usually many) packages.
It does not replace neither `make` and `ninja` nor `autoconf`, `automake`, `meson` or `cmake` and similar utilities.
Instead, xbstrap is intended to invoke those build systems in the correct order, while respecting inter-package dependencies.

**Official Discord server:** https://discord.gg/7WB6Ur3

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

## Local development

When developing `xbstrap`, you must install your local copy instead of the one provided by the `pip` repositories. To do this, run:
```
pip install --user -e .
```

### Development with Docker

For containerized builds, most `xbstrap` commands will run in two stages: once on the host, then again on the container to
actually execute the build steps. Therefore, installing `xbstrap` locally (as shown above) is not sufficient in this case.

In addition, you must change your `Dockerfile` so that instead of grabbing `xbstrap` from the `pip` repositories, it installs from the host:
1. Add the following lines (replace `/local-xbstrap` at your convenience):
```docker
ADD xbstrap /local-xbstrap
RUN pip3 install -e /local-xbstrap
```
1. Copy or symlink your local `xbstrap` into the same folder that contains the `Dockerfile`, so that it can be accessed by the previous step.
1. Rebuild the docker container as usual.

### Enabling the pre-commit hook for linting (optional)

To avoid running into the CI complaining about formatting, linting can be done in a pre-commit hook. To enable this, run:
```
git config core.hooksPath .githooks
```
