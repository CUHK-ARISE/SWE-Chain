FROM python:3.9-slim

RUN pip install uv -q

WORKDIR /app/code
COPY . .

RUN uv pip install -e . --resolution lowest --system -q && \
    uv pip install "pytest>=8,<9" pytest-json-report pytest-timeout cryptography --system -q

RUN python -c "import jwt"
