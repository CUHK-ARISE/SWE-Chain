FROM python:3.10-slim

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends \
        git cmake g++ make pkg-config autoconf automake libtool \
        meson ninja-build sudo nano && \
    rm -rf /var/lib/apt/lists/*

RUN pip install uv -q

WORKDIR /app/code
COPY . .

RUN python - <<'PY'
from pathlib import Path

path = Path("/app/code/test/conftest_user.py")
path.write_text(
    """tools_locations = {
    "cmake": {
        "3.15": {"path": {"Linux": "/usr/bin"}},
        "3.16": {"path": {"Linux": "skip-tests"}},
        "3.17": {"path": {"Linux": "skip-tests"}},
        "3.19": {"path": {"Linux": "/usr/bin"}},
        "3.23": {"path": {"Linux": "/usr/bin"}},
        "3.27": {"path": {"Linux": "/usr/bin"}},
        "4.2": {"path": {"Linux": "skip-tests"}},
    },
    "bazel": {
        "6.3.2": {"path": {"Linux": "skip-tests"}},
        "6.5.0": {"path": {"Linux": "skip-tests"}},
        "7.1.2": {"path": {"Linux": "skip-tests"}},
        "7.4.1": {"path": {"Linux": "skip-tests"}},
        "8.0.0": {"path": {"Linux": "skip-tests"}},
        "6.x": {"path": {"Linux": "skip-tests"}},
        "7.x": {"path": {"Linux": "skip-tests"}},
        "8.x": {"path": {"Linux": "skip-tests"}},
    },
    "premake": {
        "5.0.0": {"path": {"Linux": "skip-tests"}},
    },
    "qbs": {
        "2.6.0": {"path": {"Linux": "skip-tests"}},
    },
    "scons": {
        "disabled": True,
    },
    "node": {
        "disabled": True,
    },
}
"""
)
PY

RUN uv pip install -e ".[dev]" --system -q && \
    uv pip install pytest-json-report pytest-timeout --system -q && \
    sed -i "/^testpaths/d" pytest.ini

RUN groupadd -r conan && \
    useradd -r -g conan -m -d /home/conan conan && \
    echo "conan ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/conan && \
    chmod 0440 /etc/sudoers.d/conan && \
    chown -R conan:conan /app/code /home/conan

ENV HOME=/home/conan
USER conan

RUN python -c "import conan"
