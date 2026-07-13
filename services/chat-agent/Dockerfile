# Dockerfile for the chat-agent service.
#
# Build stages:
#   1. Start from an official Python 3.12 image (slim = no docs/tests, smaller layer).
#   2. Install dependencies from requirements.txt into the image.
#   3. Copy the application source files.
#   4. Launch uvicorn when the container starts.
#
# Why copy requirements.txt before the source files?
#   Docker caches each layer.  If we copied everything at once and then changed
#   a single line in main.py, Docker would re-run `pip install` from scratch.
#   Splitting the COPY means Docker only re-installs packages when
#   requirements.txt actually changes — source-only edits reuse the cached layer.

FROM python:3.12-slim

WORKDIR /app

# Layer 1: install dependencies (cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Layer 2: copy application source
COPY . .

# The PORT env var is read by config.py, but uvicorn's --port flag also needs it.
# We hard-code 8080 here to match docker-compose.yml; override with -e PORT=...
# if running the container directly on a different port.
EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
