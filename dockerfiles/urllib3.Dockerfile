FROM python:3.10-slim-bullseye

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        git \
        libffi-dev \
        libssl-dev \
        openssl; \
    rm -rf /var/lib/apt/lists/*

RUN pip install uv -q

WORKDIR /app/code
COPY . .

RUN uv pip install --system -q \
        "hatchling>=1.27,<2" \
        "hatch-vcs>=0.4,<0.6" \
        "setuptools-scm>=8,<10" \
        "setuptools<76" \
        wheel

RUN SETUPTOOLS_SCM_PRETEND_VERSION=$(python -c "ns = {}; exec(open('src/urllib3/_version.py', encoding='utf-8').read(), ns); print(ns.get('__version__') or ns.get('version'))") \
    uv pip install -e . --resolution lowest --system -q

RUN set -eux; \
    if [ -f dev-requirements.txt ]; then \
        awk ' \
            BEGIN { IGNORECASE = 1 } \
            /^[[:space:]]*#/ || /^[[:space:]]*$/ { next } \
            { \
                pkg = tolower($1); \
                sub(/[<=>!~@;[].*/, "", pkg); \
                if (pkg ~ /^(h2|freezegun|tornado|pysocks|pytest|pytest-timeout|pyopenssl|idna|trustme|cryptography|trio|hypercorn|httpx)$/) print; \
            } \
        ' dev-requirements.txt > /tmp/urllib3-test-requirements.txt; \
        uv pip install --system -q -r /tmp/urllib3-test-requirements.txt; \
        if ! grep -qi '^tornado' /tmp/urllib3-test-requirements.txt; then \
            uv pip install --system -q "tornado==6.3.3"; \
        fi; \
        if ! grep -qi '^pyOpenSSL' /tmp/urllib3-test-requirements.txt; then \
            uv pip install --system -q "pyOpenSSL==23.2.0" "idna==3.4" certifi; \
        fi; \
    else \
        uv pip install --system -q \
            "pytest>=8.3.4" \
            "pytest-timeout>=2.3.1" \
            "tornado==6.3.3" \
            "trustme>=1.2.1" \
            "cryptography>=44.0.2" \
            "pyOpenSSL>=25.0.0" \
            "idna>=3.10" \
            certifi \
            "PySocks>=1.7.1,<2" \
            "h2>=4,<5" \
            "anyio[trio]>=4.8.0" \
            "trio>=0.27.0" \
            "hypercorn @ git+https://github.com/urllib3/hypercorn@urllib3-changes" \
            "httpx>=0.28.1"; \
    fi; \
    uv pip install --system -q \
        "quart>=0.20.0" \
        "quart-trio>=0.12.0" \
        "brotli>=1.0.9" \
        "zstandard>=0.18.0" \
        "backports.zstd>=1.0.0"

RUN printf '%s\n' \
        '#!/bin/sh' \
        'if ! grep -q "localhost\\." /etc/hosts 2>/dev/null; then' \
        '    printf "127.0.0.1 localhost.\n::1 localhost.\n" >> /etc/hosts 2>/dev/null || true' \
        'fi' \
        'exec "$@"' \
        > /usr/local/bin/urllib3-entrypoint && \
    chmod +x /usr/local/bin/urllib3-entrypoint

ENTRYPOINT ["urllib3-entrypoint"]

RUN python -c "import urllib3, pytest, trustme; print(urllib3.__version__)"
