# Set up image based on Poetry / Python3.11

FROM python:3.11.6-bookworm

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
RUN poetry install --without dev --with server --no-interaction --no-ansi

# Copy app code
COPY app/ /code/app

CMD gunicorn app:docs -k uvicorn.workers.UvicornWorker -b 0.0.0.0:$PORT --workers 4
