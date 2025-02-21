# pyTincture

## Overview
`pyTincture` is a Python framework designed to leverage the capabilities of Pyodide, enabling developers to create sophisticated and user-friendly GUI libraries. This project aims to bridge the gap between Python's powerful backend and intuitive, interactive frontend interfaces.

## Features
- Pyodide Integration: Seamlessly bring Python to the web via Pyodide.
- GUI Library Support: Simplify the creation and management of GUI components in Python.
- Dynamic Code Packaging: Generate in-memory ZIP packages for frontend consumption.
- Widgetset Stub Generation: Automatically generate frontend stub classes using the @backend_for_frontend decorator.
- Authentication & Sessions: Supports Google OAuth2 and email/password authentication with session management.
- Redis Integration: Optionally use a Redis-backed session store via Upstash.
- Cross-Platform Compatibility: Works on any platform where Pyodide is supported.
- Easy to Use: Provides a user-friendly API to streamline GUI development.
- Production Launcher: Includes a uvicorn-based launcher for deploying the service.
- PyPI Distribution: Easily installable via pip from PyPI.

##Installation

From PyPI:
~~~
  pip install pyTincture
~~~

From Source:
  1. Clone the repository:
~~~
       git clone https://github.com/yourusername/pyTincture.git
       cd pyTincture
~~~

  2. Install dependencies:
~~~
     pip install .
~~~
   (Alternatively, follow the instructions in pyproject.toml.)

##Running the Service
-------------------
Development Mode:
  Start the FastAPI application with Uvicorn:
~~~
     uvicorn pytincture.backend.app:app --host 0.0.0.0 --port 8070
~~~

Production Launcher:
  Run the included launcher:
~~~
     python -m pytincture
~~~
  (This launcher in pytincture/__init__.py sets up necessary environment variables such as MODULES_PATH and starts uvicorn with your service.)


## Requirements
- Python 3.x
- Pyodide

## Docker Quick Start Example built from https://github.com/pytincture/pytincture_example
Run the docker image directly from Dockerhub
~~~
docker run -p8070:8070 -i pytincture/pytincture:latest
~~~
Load url in browser
~~~
http://localhost:8070/py_ui
~~~


## License
`pyTincture` is licensed under the MIT License.


