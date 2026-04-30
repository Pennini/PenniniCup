# Use a supported Debian base to avoid EOL apt repositories
FROM python:3.12-slim-bookworm

# Set the working directory in the container
WORKDIR /opt/project

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=.
ENV PENNINICUP_IN_DOCKER=true
ENV POETRY_VIRTUALENVS_CREATE=false

# Install dependencies
RUN set -xe \
    && apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && pip install --no-cache-dir poetry==1.8.4 setuptools \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY ["poetry.lock", "pyproject.toml", "./"]
RUN poetry install --only main --no-root --no-interaction --no-ansi

# Copy project files
COPY ["README.md", "Makefile", "./"]
COPY src src
COPY local local

# Expose the Django development server port (adjust if needed)
EXPOSE 8000

# Set up the entrypoint
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod a+x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
