# pyTincture

## Overview
`pyTincture` is a Python framework designed to leverage the capabilities of Pyodide, enabling developers to create sophisticated and user-friendly GUI libraries. This project aims to bridge the gap between Python's powerful backend and intuitive, interactive frontend interfaces.

## Features
- **Pyodide Integration**: Seamlessly integrates with Pyodide to bring Python to the web.
- **GUI Library Support**: Simplifies the process of creating and managing GUI components in Python.
- **Cross-Platform Compatibility**: Works across various platforms where Pyodide is supported.
- **Easy to Use**: User-friendly API that makes GUI development in Python more accessible.

## Requirements
- Python 3.x
- Pyodide

## Docker Quick Start
Run the docker image directly from Dockerhub
~~~
docker run -p8070:8070 -i pytincture/pytincture:example044
~~~
Load url in browser
~~~
http://localhost:8070/py_ui
~~~

## Docker build from repo
Docker command to build this example is:
~~~
docker build . -t pytincture:example -f Dockerfile-example
~~~
Then the docker command to run it is:
~~~
docker run -p8070:8070 -i pytincture:example
~~~
Load url in browser
~~~
http://localhost:8070/py_ui
~~~


## License
`pyTincture` is licensed under the MIT License.


