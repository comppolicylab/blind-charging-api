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
    liblzma-dev

# Install pyenv / latest Python version
WORKDIR /root
RUN git clone https://github.com/pyenv/pyenv.git ~/.pyenv
ENV PYENV_ROOT="/root/.pyenv"
ENV PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
RUN pyenv install 3.12.7
RUN pyenv global 3.12.7

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

# Install private BC2 repo
RUN --mount=type=ssh python3 -m pip install git+ssh://git@github.com/stanford-policylab/bc2.git@main
