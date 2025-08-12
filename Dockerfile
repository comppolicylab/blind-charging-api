FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS buildbox

RUN apt-get update && apt-get install -y apt-transport-https curl gnupg2 git
RUN curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
RUN curl https://packages.microsoft.com/config/ubuntu/18.04/prod.list > /etc/apt/sources.list.d/mssql-release.list
RUN apt-get update \
    && apt-get install -y -V openssh-client \
    && apt-get install -y -V tesseract-ocr \
    && apt-get install -y -V unixodbc-dev \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql17
# Set up SSH
RUN mkdir -p ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts

ENV PYTHONFAULTHANDLER=1 \
      PYTHONUNBUFFERED=1 \
      PYTHONHASHSEED=random

WORKDIR /code

# Copy dependency manifests
COPY uv.lock pyproject.toml README.md /code/

# Install dependencies
RUN --mount=type=ssh uv sync --locked
# Pre-load encodings from tiktoken since download is not available in most environments
RUN python -c 'import tiktoken; [tiktoken.get_encoding(enc) for enc in ["cl100k_base", "o200k_base"]]'

# Copy app code
COPY alembic.ini /code/
COPY alembic/ /code/alembic
COPY app/ /code/app
COPY cli/ /code/cli
COPY terraform/ /code/terraform

# Set up default config file
COPY config.toml /config/
ENV CONFIG_PATH=/config/config.toml

ENTRYPOINT [ "python", "-m", "cli" ]
CMD [ "api", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers" ]
