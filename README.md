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

To install your local copy, run the following command:
```
pip install --user -e .
```

### Using Docker

For containerized builds, most `xbstrap` commands will run in two stages: once on the host, then again on the container to
actually execute the build steps. Therefore, installing `xbstrap` locally (as shown above) is not sufficient in this case.

In addition, you must change the `Dockerfile` so that instead of grabbing `xbstrap` from the `pip` repositories, it installs from the host:
1. Remove the `pip3 install xbstrap` line.
1. Add the following lines:
```docker
ADD xbstrap /var/bootstrap-managarm/local-xbstrap
RUN pip3 install -e /var/bootstrap-managarm/local-xbstrap
```
1. Copy or symlink your local `xbstrap` into `src/docker`, so that it can be accessed by the previous step.
1. Rebuild the docker container as usual with `docker build -t managarm-buildenv --build-arg=USER=$(id -u) src/docker`
