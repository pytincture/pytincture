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

## Installation

From PyPI:
~~~
pip install pytincture
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

## Environment Variables
- MODULES_PATH: Directory containing module files used for dynamic packaging.
- USE_REDIS_INSTANCE: Set to "true" to enable Redis-backed session storage.
- ALLOWED_EMAILS: A comma-separated list of authorized email addresses.
   example: "some@email.com,joe@email.com"
- ENABLE_GOOGLE_AUTH: Enable the respective authentication mechanisms.
   example: "true"
- ALLOWED_NOAUTH_CLASSCALLS
   example: [{"file": "somefile.py", "class": "SomeClass", "function": "somefunction"}]
- GOOGLE_CLIENT_ID
- GOOGLE_CLIENT_SECRET
- SECRET_KEY: Secret key for google auth
- USE_REDIS_INSTANCE: Enable the redis upstash for sessions
   example: "true"
- REDIS_UPSTASH_INSTANCE_URL: Url for upstash redis instance
   example: "http://127.0.0.1:16379"
- REDIS_UPSTASH_INSTANCE_TOKEN: Redis Upstash token
- DATABASE_URL: Database connection string
   example: "sqlite:////absolute/path/to/database.db"

## Running the Service with your application
-------------------
Development Mode:

  Use the service from your application:
~~~
if __name__=="__main__":
    from pytincture import launch_service
    launch_service(env_vars={
      "ALLOWED_EMAILS": []
      "MODULES_PATH": "."  # current path
    })
~~~

## Testing

Tests are written using pytest and cover endpoints, helper functions, and the launcher.
Run all tests with:
~~~
python -m pytest
~~~

Tests include:

  - tests/test_app.py: Endpoint tests and service logic.
  - tests/test_dataclass.py: Tests for stub generation, decorators, and helper functions.
  - tests/test_launcher.py: Tests for the uvicorn launcher and process management.


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


