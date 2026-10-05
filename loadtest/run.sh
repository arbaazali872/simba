#!/bin/bash

# Starts a throwaway copy of SIMBA configured like production, for the load test:
#   - web limited to 2 CPUs (production has 2), so Gunicorn starts 4 workers as on the server
#   - RAM limits close to production's 3.8 GB machine
#   - an empty in-memory database, filled with test users by seed.py
#   - no way out to Gmail, OpenAI, Together or Mistral (error emails and AI calls fail on the spot)
# The web service answers on http://localhost:8001. Remove everything with loadtest/stop.sh.
# Usage (Git Bash, from the repo root):  loadtest/run.sh [number of students, default 500]

set -e
# Git Bash on Windows rewrites paths like /code; docker must get them as written (curl must not)
docker() { MSYS_NO_PATHCONV=1 command docker "$@"; }
cd "$(dirname "$0")/.."

STUDENTS="${1:-500}"
IMAGE="${IMAGE:-simba-web:latest}"
BLOCKED_HOSTS="--add-host smtp.gmail.com:127.0.0.1 --add-host api.openai.com:127.0.0.1 --add-host api.together.ai:127.0.0.1 --add-host api.mistral.ai:127.0.0.1"

echo "Starting the test database..."
docker network create lt-net > /dev/null
docker run -d --name lt-db --network lt-net --memory 1g --tmpfs /var/lib/postgresql/data \
    -e POSTGRES_DB=simba_db -e POSTGRES_USER=simba_user -e POSTGRES_PASSWORD=loadtest \
    postgres:16 > /dev/null
# -h localhost: only true once the real server listens, not during initdb's temporary start
until docker exec lt-db pg_isready -h localhost -U simba_user > /dev/null 2>&1; do sleep 1; done

echo "Starting web ($IMAGE, 2 CPUs, 2 GB)..."
docker run -d --name lt-web --network lt-net --cpuset-cpus 0,1 --memory 2g -p 8001:8000 $BLOCKED_HOSTS \
    -e SECRET_KEY=loadtest-only -e DEBUG=False -e ALLOWED_HOSTS=localhost,127.0.0.1 -e ENVIRONMENT=production \
    -e DB_HOST=lt-db -e DB_PORT=5432 -e POSTGRES_DB=simba_db -e POSTGRES_USER=simba_user -e POSTGRES_PASSWORD=loadtest \
    "$IMAGE" sh /code/entrypoint.sh > /dev/null
for i in $(seq 1 120); do
    curl -s -o /dev/null http://localhost:8001/login/ && break
    sleep 2
done
curl -s -o /dev/null -w "Web answers: HTTP %{http_code}\n" http://localhost:8001/login/
docker logs lt-web 2>&1 | grep -E "Starting Gunicorn|Booting worker" | head -n 6 || true

echo "Creating $STUDENTS test students..."
docker exec -i -w /code lt-web python - "$STUDENTS" < loadtest/seed.py > loadtest/data.json
echo "Done: loadtest/data.json written. Run the scenarios as described in loadtest/README.md."
