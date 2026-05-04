FROM python:3.10-slim

ARG PYGMENTS_SPEC="pygments>=2.14,<2.19"
ARG EXCEPTIONGROUP_NO_PATCH=

RUN pip install uv -q

WORKDIR /app/code
COPY . .

# pytest uses setuptools-scm which requires git; use SETUPTOOLS_SCM_PRETEND_VERSION
RUN SETUPTOOLS_SCM_PRETEND_VERSION=$(python -c "import re; print(re.search(r'version\\s*=\\s*[\\\"\\x27]([^\\\"\\x27]+)[\\\"\\x27]', open('src/_pytest/_version.py').read()).group(1))") \
    uv pip install -e . --resolution lowest --system -q

RUN uv pip install pytest-timeout "hypothesis==6.68.0" mock nose requests argcomplete xmlschema numpy pexpect twisted xdist jinja2 decorator xdist asynctest \
    "setuptools==65.7.0" "iniconfig>=1.1.0" "packaging>=20.0" \
    "pluggy>=1.0.0,<1.4" "py>=1.10.0" "attrs==21.4.0" "tomli>=1.0.0" \
    "${PYGMENTS_SPEC}" --system -q

RUN uv pip install "importlib-metadata>=4.6" --system -q 2>/dev/null || true
ENV EXCEPTIONGROUP_NO_PATCH=${EXCEPTIONGROUP_NO_PATCH}
ENV PYTEST_ADDOPTS="-o xfail_strict=false"

RUN python -c "import pytest"
