# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# TODO(developer): Set your name
# Copyright © 2023 <your name>

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the "Software"), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

import codecs
import os
import re
from io import open
from os import path

from setuptools import setup, find_packages


def read_requirements(path):
    with open(path, "r") as f:
        requirements = f.read().splitlines()
        processed_requirements = []

        for req in requirements:
            # For git or other VCS links
            if req.startswith("git+") or "@" in req:
                pkg_name = re.search(r"(#egg=)([\w\-_]+)", req)
                if pkg_name:
                    processed_requirements.append(pkg_name.group(2))
                else:
                    # You may decide to raise an exception here,
                    # if you want to ensure every VCS link has an #egg=<package_name> at the end
                    continue
            else:
                processed_requirements.append(req)
        return processed_requirements


here = path.abspath(path.dirname(__file__))

with open(path.join(here, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

# loading version from setup.py
with codecs.open(
    os.path.join(here, "ridges/__init__.py"), encoding="utf-8"
) as init_file:
    version_match = re.search(
        r"^__version__ = ['\"]([^'\"]*)['\"]", init_file.read(), re.M
    )
    version_string = version_match.group(1)

setup(
    name="ridges",
    version=version_string,
    description="ridges",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/taoagents/ridges",
    author="Ridges",
    packages=find_packages(),
    include_package_data=True,
    author_email="taogods@proton.me",
    license="MIT",
    python_requires=">=3.8",
    install_requires=[
        # Install nightly version to patch issue with validators crashing in 1.8.0 release
        "websocket-client @ git+https://github.com/websocket-client/websocket-client.git",
        "bittensor==9.0.0",
        "trueskill",
        "starlette>=0.30.0",
        "pydantic>=2",
        "requests_oauthlib",
        "pylint",
        "rich>=13",
        "pytest>=8",
        "pytest-json-report",
        "pytest-cov",
        "pytest-asyncio",
        "numpy>=1",
        "setuptools>=68",
        "ipython",
        "ipdb",
        "swebench>=4.0.3",
        "boto3",
        "openai>=1.0.0",
        "pygithub",
        "pytz",
        "posthog",
        "tiktoken",
        "GitPython",
        "pyyaml",
        "matplotlib",
        "nbformat",
        "nbconvert",
        "coverage",
        "traitlets",
        "numpy",
        "pandas",
        "ipython",
        "PyYAML",
        "Jinja2",
        "pytest",
        "pillow",
        "cycler",
        "Pygments",
        "bittensor",
        "starlette",
        "config",
        "pydantic",
        "cryptography",
        "Flask",
        "requests",
        "httpx",
        "Werkzeug",
        "PyJWT",
        "anyio",
        "setuptools",
        "six",
        "wheel",
        "packaging",
        "pip",
        "python-dateutil",
        "retry",
        "dill",
        "cloudpickle",
        "botocore",
        "boto3",
        "pyparsing",
        "click",
        "jsonschema",
        "urllib3",
        "aiohttp",
        "yarl",
        "pytz",
        "multidict",
        "rich",
        "simple-parsing",
        "posthog",
        "python-dotenv",
        "GitPython",
        "tabulate",
        "openai",
        "tiktoken",
        "ipdb",
        "bleach",
        "beautifulsoup4",
        "soupsieve",
        "gitdb",
        "attrs",
        "distro",
        "filelock",
        "decorator",
        "parso",
        "jedi",
        "colorama",
        "markdown-it-py",
        "typing_extensions",
        "notebook",
        "idna",
        "propcache",
        "ghapi",
        "fastcore",
        "smmap",
        "mdurl",
        "charset-normalizer",
        "overrides",
        "cffi",
        "docker",
        "fsspec",
        "pyarrow",
        "MarkupSafe",
        "py",
        "xxhash",
        "async-timeout",
        "aiosignal",
        "frozenlist",
        "aiohappyeyeballs",
        "certifi",
        "chardet",
        "stack-data",
        "matplotlib-inline",
        "exceptiongroup",
        "jupyter_client",
        "prompt_toolkit",
        "wcwidth",
        "pexpect",
        "ptyprocess",
        "tzdata",
        "unidiff",
        "huggingface-hub",
        "tqdm",
        "multiprocess",
        "datasets",
        "arrow",
        "identify",
        "asttokens",
        "executing",
        "pure_eval",
        "pre_commit",
        "cfgv",
        "tomli",
        "more-itertools",
        "platformdirs",
        "virtualenv",
        "distlib",
        "fastapi",
        "defusedxml",
        "pluggy",
        "Deprecated",
        "iniconfig",
        "contourpy",
        "fonttools",
        "ipykernel",
        "tornado",
        "kiwisolver",
        "swebench",
        "anthropic",
        "tenacity",
        "Flask-SocketIO",
        "rich-argparse",
        "Flask-Cors",
        "together",
        "groq",
        "gymnasium",
        "PyGithub",
        "bert-score"
    ],
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Topic :: Software Development :: Build Tools",
        # Pick your license as you wish
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Topic :: Scientific/Engineering",
        "Topic :: Scientific/Engineering :: Mathematics",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
        "Topic :: Software Development",
        "Topic :: Software Development :: Libraries",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)
