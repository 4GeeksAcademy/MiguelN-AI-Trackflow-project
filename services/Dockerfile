FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

RUN pip install --no-cache-dir uv

COPY requirements.txt pyproject.toml ./
RUN uv pip install --system -r requirements.txt

COPY services ./services
COPY scripts ./scripts
COPY packages/shared ./packages/shared
COPY data/__init__.py ./data/__init__.py
COPY data/pipelines ./data/pipelines
COPY data/process ./data/process
RUN uv pip install --system --no-deps --editable .

EXPOSE 8000
CMD ["uvicorn", "services.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/services", "--reload-dir", "/app/packages/shared/trackflow_shared"]