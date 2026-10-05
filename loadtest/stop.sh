#!/bin/bash

# Removes everything run.sh started. The test database lives in memory, so nothing is left behind.
# Usage (Git Bash, from the repo root):  loadtest/stop.sh

docker rm -f lt-web lt-db > /dev/null 2>&1
docker network rm lt-net > /dev/null 2>&1
echo "Load-test containers and network removed."
