#!/usr/bin/env bash
set -ex

cd /code
ls -al .
ls -al ../config

uvicorn app:docs --host 0.0.0.0 --port $PORT --workers 1 --app-dir /code/
