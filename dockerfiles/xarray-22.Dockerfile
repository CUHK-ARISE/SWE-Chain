FROM python:3.10-slim

# HDF5 (Debian's libhdf5 / netCDF4 wheel) is not thread-safe; limit dask to 1
# worker to avoid segfault in test_open_mfdataset_manyfiles and NetCDF HDF errors.
ENV DASK_NUM_WORKERS=1
ENV ZARR_V3_EXPERIMENTAL_API=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential libhdf5-dev libnetcdf-dev && \
    rm -rf /var/lib/apt/lists/*

RUN pip install uv -q

WORKDIR /app/code
COPY . .

RUN SETUPTOOLS_SCM_PRETEND_VERSION=$(grep "^Version:" PKG-INFO | cut -d' ' -f2) \
    uv pip install -e . --no-build-isolation --system -q && \
    uv pip install \
        "numpy<2" "pandas<1.5" "scipy<1.14" cftime \
        pytest pytest-cov pytest-env pytest-xdist pytest-timeout pytest-json-report \
        pytest-asyncio pytz tzdata \
        "dask[array]<2023.5" netCDF4 "zarr<3" bottleneck "matplotlib<3.8" numbagg "pint<0.22" "sparse<0.14" flox \
        --system -q

RUN sed -i 's/--mypy[^ "]*//g' pyproject.toml || true

RUN python -c "import xarray"
