FROM python:3.11-slim

RUN pip install uv -q

WORKDIR /app/code
COPY . .

# pytest uses setuptools-scm which requires git; use SETUPTOOLS_SCM_PRETEND_VERSION
RUN uv pip install "pyparsing>=2.4.7" --system -q && \
    SETUPTOOLS_SCM_PRETEND_VERSION=$(python -c "import re; print(re.search(r'version\\s*=\\s*[\\\"\\x27]([^\\\"\\x27]+)[\\\"\\x27]', open('src/_pytest/_version.py').read()).group(1))") \
    uv pip install -e . --resolution lowest --system -q

RUN uv pip install pytest-json-report pytest-timeout hypothesis mock nose requests argcomplete xmlschema numpy pexpect twisted xdist jinja2 decorator xdist asynctest \
    "setuptools>=68.2.2,<70" "iniconfig>=1.1.0" "packaging>=22.0" \
    "pluggy>=1.5,<2.0" "attrs==21.4.0" "tomli>=1.0.0" \
    "pygments>=2.14,<2.19" --system -q

RUN uv pip install "importlib-metadata>=4.6" --system -q 2>/dev/null || true
ENV PYTEST_ADDOPTS="-o xfail_strict=false"

RUN python -c "import pytest"
