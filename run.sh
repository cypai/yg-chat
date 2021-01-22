#!/usr/bin/env bash

gunicorn app.main:app -w 1 -k uvicorn.workers.UvicornWorker -c gunicorn_conf.py
