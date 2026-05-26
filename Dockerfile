FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir -e .

CMD ["python", "-m", "app.main"]
