#!/bin/sh
alembic upgrade head
exec python server.py