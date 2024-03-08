"""File required by pip to turn folder into an official Python package"""
from os import path
from setuptools import setup, find_packages
import pkg_resources


base_path = path.dirname(__file__)
version = {}

# Read the requirements.txt file to compile packages needed
with open('requirements.txt') as reqs:
    install_requires = reqs.read().splitlines()

setup(
    name='pytincture',
    version='0.3.0',
    description=(
        'UI Builder'
    ),
    packages = find_packages(),
    install_requires=install_requires,
    include_package_data=True
)
