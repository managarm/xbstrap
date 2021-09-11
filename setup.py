#!/usr/bin/python3

import os
import shutil

from setuptools import find_packages, setup
from setuptools.command.develop import develop
from setuptools.command.install import install

with open("README.md", "r") as f:
    readme = f.read()


class CompletionDevelop(develop):
    def run(self):
        if os.access("/etc/bash_completion.d", os.W_OK):
            shutil.copyfile("extrafiles/completion.sh", "/etc/bash_completion.d/xbstrap")
        else:
            print(
                "Insufficient permissions to install the bash completion script to"
                " /etc/bash_completion.d"
            )
        if os.access("/usr/share/fish/vendor_completions.d/", os.W_OK):
            shutil.copyfile(
                "extrafiles/completion.fish", "/usr/share/fish/vendor_completions.d/xbstrap.fish"
            )
        else:
            print(
                "Insufficient permissions to install the fish completion script to"
                " /usr/share/fish/vendor_completions.d"
            )
        develop.run(self)


class CompletionInstall(install):
    def run(self):
        if os.access("/etc/bash_completion.d", os.W_OK):
            shutil.copyfile("extrafiles/completion.sh", "/etc/bash_completion.d/xbstrap")
        else:
            print(
                "Insufficient permissions to install the bash completion script to"
                " /etc/bash_completion.d"
            )
        if os.access("/usr/share/fish/vendor_completions.d/", os.W_OK):
            shutil.copyfile(
                "extrafiles/completion.fish", "/usr/share/fish/vendor_completions.d/xbstrap.fish"
            )
        else:
            print(
                "Insufficient permissions to install the fish completion script to"
                " /usr/share/fish/vendor_completions.d"
            )
        install.run(self)


setup(
    name="xbstrap",
    version="0.25.3",
    packages=find_packages(),
    package_data={"xbstrap": ["schema.yml"]},
    install_requires=[
        "colorama",
        "jsonschema",
        "pyyaml",
        "zstandard",  # For xbps support.
    ],
    extras_require={
        "test": [
            "black",
            "flake8",
            "pep8-naming",
            "flake8-isort",
        ]
    },
    cmdclass={
        "develop": CompletionDevelop,
        "install": CompletionInstall,
    },
    entry_points={
        "console_scripts": [
            "xbstrap = xbstrap:main",
            "xbstrap-pipeline = xbstrap.pipeline:main",
            "xbstrap-mirror = xbstrap.mirror:main",
        ]
    },
    # Package metadata.
    author="Alexander van der Grinten",
    author_email="alexander.vandergrinten@gmail.com",
    license="MIT",
    url="https://github.com/managarm/xbstrap",
    long_description=readme,
    long_description_content_type="text/markdown",
)
