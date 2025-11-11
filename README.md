# pyTincture

## Overview
`pyTincture` is a Python framework designed to leverage the capabilities of Pyodide, enabling developers to create sophisticated and user-friendly GUI libraries. This project aims to bridge the gap between Python's powerful backend and intuitive, interactive frontend interfaces.

## Features
- Pyodide Integration: Seamlessly bring Python to the web via Pyodide.
- GUI Library Support: Simplify the creation and management of GUI components in Python.
- Dynamic Code Packaging: Generate in-memory ZIP packages for frontend consumption.
- Widgetset Stub Generation: Automatically generate frontend stub classes using the @backend_for_frontend decorator.
- Streaming BFF Calls: Enable true streaming responses with the @bff_stream decorator.
- Authentication & Sessions: Supports Google OAuth2, SAML 2.0 SSO, and email/password authentication with session management.
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
- MODULES_PATH: Directory containing module files used for dynamic packaging. This is set automatically from `modules_folder` when `launch_service` starts; overriding it via env vars is usually unnecessary.
- USE_REDIS_INSTANCE: Set to "true" to enable Redis-backed session storage.
- ALLOWED_EMAILS: A comma-separated list of authorized email addresses.
   example: "some@email.com,joe@email.com"
- ENABLE_GOOGLE_AUTH: Enable the respective authentication mechanisms.
   example: "true"
- ENABLE_SAML_AUTH: Enable SAML 2.0 authentication.
   example: "true"
- SAML_EMAIL_ATTRIBUTE: Attribute name used to extract the user email from the SAML assertion.
   example: "email"
- SAML_NAME_ATTRIBUTE: Optional attribute for the display name.
   example: "givenName"
- SAML_DEFAULT_REDIRECT: Optional redirect path or URL template after SAML login (defaults to `/{application}` when unset).
   example: "/{application}"
- SAML_SP_ENTITY_ID: Optional template for the SP entity ID (supports {application}, {base_url}, {host}); defaults to `/{application}/auth/saml/metadata`.
- SAML_SP_ASSERTION_CONSUMER_SERVICE_URL: Optional template for the ACS endpoint (supports placeholders like {application}).
- SAML_SP_X509_CERT: Service Provider certificate in PEM format if signing/encryption is required.
- SAML_SP_PRIVATE_KEY: Service Provider private key in PEM format matching the SP certificate.
- SAML_IDP_ENTITY_ID: Identity Provider entity ID.
- SAML_IDP_SSO_URL: Identity Provider SSO URL.
- SAML_IDP_SLO_URL: Optional Identity Provider SLO URL.
- SAML_IDP_X509_CERT: Identity Provider certificate in PEM format.
- SAML_DEBUG: Enable verbose SAML logging.
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
    launch_service(
        modules_folder=".",  # point to your modules directly
        env_vars={
            "ALLOWED_EMAILS": []
        }
    )
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
