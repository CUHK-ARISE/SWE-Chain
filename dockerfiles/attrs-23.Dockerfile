FROM python:3.10-slim

RUN pip install uv -q

WORKDIR /app/code
COPY . .

RUN SETUPTOOLS_SCM_PRETEND_VERSION=$(grep "^Version:" PKG-INFO | head -1 | cut -d' ' -f2) \
    uv pip install -e . --resolution lowest --system -q

RUN uv pip install "pytest>=4.3" pytest-json-report pytest-timeout \
        hypothesis pympler zope.interface cloudpickle pytest-xdist six \
        --system -q

RUN python -c "import attrs"
