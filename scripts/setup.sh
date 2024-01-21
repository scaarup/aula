#!/usr/bin/env bash

set -e
cd "$(dirname "$0")/.."
python3 -m pip --disable-pip-version-check --no-cache-dir install --requirement requirements.txt