FROM rocker/tidyverse:4.4.1

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git-all \
    libcurl4-openssl-dev \
    libssl-dev \
    make \
    build-essential \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    llvm \
    libncurses5-dev \
    libncursesw5-dev \
    libffi-dev \
    liblzma-dev \
    openssh-client \
    apt-transport-https \
    gnupg2
RUN curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
RUN curl https://packages.microsoft.com/config/ubuntu/22.04/prod.list > /etc/apt/sources.list.d/mssql-release.list
RUN apt-get update && \
    apt-get install -y -V unixodbc-dev && \
    ACCEPT_EULA=Y apt-get install -y msodbcsql18

# NOTE: in the GitHub action, this is where the SSH configs will be injected.

WORKDIR /root
# Install pyenv / latest Python version
ENV PYENV_ROOT="/usr/local/.pyenv"
RUN git clone https://github.com/pyenv/pyenv.git $PYENV_ROOT
RUN chmod -R 777 $PYENV_ROOT
ENV PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
# Set permissions for everyone to access pyenv
RUN pyenv install 3.12.7
RUN pyenv global 3.12.7
RUN python3 -m pip install --upgrade pip
# Set variables for rstudio user
RUN cat <<EOF >> /home/rstudio/.bashrc
export PYENV_ROOT="/usr/local/pyenv"
export PATH="\$PYENV_ROOT/shims:\$PYENV_ROOT/bin:\$PATH"
EOF

# Install R packages
RUN install2.r --error --skipmissing --skipinstalled -n $(nproc --all) \
    askpass \
    base64enc \
    dplyr \
    glue \
    grid \
    janitor \
    jsonlite \
    parallel \
    png \
    purrr \
    qpdf \
    readxl \
    reticulate \
    rstudioapi \
    stringr \
    tibble \
    tidyr \
    tidyverse \
    writexl \
    yaml
# Install `diffmatchpatch` from Alex's fork which fixes a buffer overflow error.
RUN installGithub.r chohlasa/diffmatchpatch@348b333

# Set default R directory to /data
RUN cat <<EOF > /etc/rstudio/rsession.conf
session-default-working-dir=/data
session-default-new-project-dir=/data/projects
EOF

# Install Python stuff
ENV PYTHONFAULTHANDLER=1 \
      PYTHONUNBUFFERED=1 \
      PYTHONHASHSEED=random \
      PIP_NO_CACHE_DIR=off \
      PIP_DISABLE_PIP_VERSION_CHECK=on \
      PIP_DEFAULT_TIMEOUT=100 \
      POETRY_VIRTUALENVS_CREATE=false
RUN python3 -m pip install pyodbc poetry
COPY poetry.lock pyproject.toml /root/
RUN mkdir -p ~/.ssh && ssh-keyscan github.com >> ~/.ssh/known_hosts
RUN --mount=type=ssh python3 -m poetry install --without-dev --no-interaction --no-ansi --no-root
