#!/usr/bin/env bash

set -e

# If the directory /data exists, give it to the `rstudio` user
echo "Checking for /data directory"
if [ -d /data ]; then
  echo "Found /data directory, giving it to rstudio user"
  chown -R rstudio:rstudio /data
fi

# Delegate to the real init script
exec /init
