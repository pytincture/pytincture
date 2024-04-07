"""File required by pip to turn folder into an official Python package"""
from os import path
from setuptools import setup, find_packages
import pkg_resources


base_path = path.dirname(__file__)
version = {}

# Read the requirements.txt file to compile packages needed
with open('requirements.txt') as reqs:
    install_requires = reqs.read().splitlines()

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name='pytincture',
    version='0.4.4',
    description=(
        'UI Builder'
    ),
    url="https://github.com/pytincture/pytincture",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages = find_packages(),
    install_requires=install_requires,
    include_package_data=True
)
