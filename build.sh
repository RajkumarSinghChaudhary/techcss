#!/usr/bin/env bash
# exit on error
set -o errexit

pip install -r requirements.txt

# Ensure the uploads directory exists for screenshots
mkdir -p uploads