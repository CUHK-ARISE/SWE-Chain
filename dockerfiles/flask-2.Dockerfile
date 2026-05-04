FROM python:3.9-slim

RUN pip install uv -q

WORKDIR /app/code
COPY . .

RUN uv pip install -e . --resolution lowest --system -q && \
    uv pip install pytest-json-report pytest-timeout "pytest==6.2.4" "pytest-metadata<2" "setuptools<67.5" \
        "importlib-metadata>=4.6" blinker greenlet asgiref python-dotenv --system -q

RUN python -c "import flask"
