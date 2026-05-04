FROM python:3.9-slim

RUN pip install uv -q

WORKDIR /app/code
COPY . .

RUN sed -i 's/^\[pytest\]$/[tool:pytest]/' setup.cfg 2>/dev/null || true

RUN uv pip install "MarkupSafe>=1.0,<2.0" "setuptools<67.5" --system -q && \
    uv pip install -e . --resolution lowest --system -q && \
    uv pip install "pytest>=3,<7.2" pytest-json-report pytest-timeout --system -q && \
    uv pip install trio --system -q 2>/dev/null || true

RUN python -c "import jinja2"
