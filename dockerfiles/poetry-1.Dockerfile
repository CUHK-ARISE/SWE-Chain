FROM python:3.10-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc make git libc6-dev bash && \
    rm -rf /var/lib/apt/lists/*

RUN pip install uv -q

ENV SHELL=/bin/bash \
    CFLAGS="-Wno-error=int-conversion -Wno-int-conversion" \
    PIP_CONSTRAINT=/tmp/poetry-test-constraints.txt

RUN printf 'setuptools<70\nvirtualenv<20.26\n' > /tmp/poetry-test-constraints.txt

WORKDIR /app/code
COPY . .

RUN sed -i 's/-n auto//g' pyproject.toml || true

RUN uv pip install -e . --system -q && \
    uv pip install \
        deepdiff httpretty responses jaraco-classes cachy \
        pytest pytest-mock "pytest-xdist[psutil]" \
        pytest-json-report pytest-timeout \
        "virtualenv>=20.23,<20.26" "setuptools>=67.6.1,<70" wheel \
        "lockfile>=0.12.2" "jsonschema>=4.10,<4.18" "html5lib>=1.1,<2" "filelock>=3.8,<4" \
        --system -q && \
    uv pip install --system -q --reinstall "urllib3<2"

RUN python -m poetry lock --no-update -q

RUN python -c "import poetry"
