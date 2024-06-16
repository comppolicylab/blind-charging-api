# Set up image based on Poetry / Python3.11

FROM python:3.11.6-bookworm

# Set up SSH
RUN apt-get update && apt-get install -y openssh-client
RUN mkdir -p ~/.ssh
RUN ssh-keyscan github.com >> ~/.ssh/known_hosts

ENV PYTHONFAULTHANDLER=1 \
      PYTHONUNBUFFERED=1 \
      PYTHONHASHSEED=random \
      PIP_NO_CACHE_DIR=off \
      PIP_DISABLE_PIP_VERSION_CHECK=on \
      PIP_DEFAULT_TIMEOUT=100 \
      POETRY_VIRTUALENVS_CREATE=false

# Set up poetry
RUN pip install poetry==1.5.1

WORKDIR /code

# Copy dependency manifests
COPY poetry.lock pyproject.toml README.md /code/

# Install dependencies
RUN --mount=type=ssh poetry install --without dev --no-interaction --no-ansi

# Copy app code
COPY config.toml /config/
COPY alembic.ini /code/
COPY alembic/ /code/alembic
COPY app/ /code/app

ENV CONFIG_PATH=/config/config.toml

CMD uvicorn app:docs --host 0.0.0.0 --port $PORT --workers 1 --app-dir /code/
