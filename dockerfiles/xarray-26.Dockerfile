FROM python:3.11-slim

# HDF5 (Debian's libhdf5 / netCDF4 wheel) is not thread-safe; limit dask to 1
# worker to avoid segfault in test_open_mfdataset_manyfiles and NetCDF HDF errors.
ENV DASK_NUM_WORKERS=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libhdf5-dev libnetcdf-dev && \
    rm -rf /var/lib/apt/lists/*

RUN pip install uv -q

WORKDIR /app/code
COPY . .

RUN SETUPTOOLS_SCM_PRETEND_VERSION=$(grep "^Version:" PKG-INFO | cut -d' ' -f2) \
    uv pip install -e . --no-build-isolation --system -q && \
    uv pip install \
        "pandas>=2.2,<3" scipy cftime \
        pytest pytest-cov pytest-env pytest-xdist pytest-timeout pytest-json-report \
        pytest-asyncio pytz tzdata \
        "dask[array]" netCDF4 "zarr<3" bottleneck matplotlib numbagg pint sparse flox pyarrow \
        --system -q

RUN sed -i 's/--mypy[^ "]*//g' pyproject.toml || true

RUN python -c "import xarray"
