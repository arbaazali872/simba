#!/bin/bash
# Usage: ./dev.sh           rebuild and restart, keeping the database
#        ./dev.sh --reset   also wipe the database (fresh seed data)

echo "🚀 Starting SIMBA Development Environment"
echo "========================================"

if [ ! -f .env ]; then
    echo "⚠️  Warning: .env file not found!"
    echo "Please create a .env file with required environment variables."
    exit 1
fi

export ENVIRONMENT=development

# Plain "down" keeps the database between runs. The old script always used
# "down -v", which wiped your data every time; that was only needed because the
# code used to live in a volume (see docker-compose.yml).
if [ "$1" = "--reset" ]; then
    echo "🛑 Stopping existing containers and wiping volumes (database included)..."
    docker compose down -v
else
    echo "🛑 Stopping existing containers (database kept; use --reset to wipe it)..."
    docker compose down
fi

echo "🏗️  Building and starting containers..."
if ! docker compose up --build -d; then
    echo "❌ Build or startup failed, see the output above."
    exit 1
fi

# Wait until a URL answers, up to $3 seconds. The old script slept a fixed 10 s,
# but web and Chainlit need longer than that, so a healthy start was always
# reported as failed (and a real failure was still reported as "ready").
wait_for() {
    local name=$1 url=$2 timeout=$3 waited=0
    printf "⏳ Waiting for %s" "$name"
    until curl -s -o /dev/null "$url"; do
        if [ "$waited" -ge "$timeout" ]; then
            echo ""
            echo "❌ $name did not respond at $url within ${timeout}s"
            return 1
        fi
        printf "."
        sleep 5
        waited=$((waited + 5))
    done
    echo " ✅ ${waited}s"
}

failed=0
wait_for "web" http://localhost:8000 180 || { failed=1; echo "📋 Web logs:"; docker compose logs web | tail -20; }
wait_for "Chainlit" http://localhost:8500 180 || { failed=1; echo "📋 Chainlit logs:"; docker compose logs chainlit | tail -20; }

echo ""
docker compose ps
echo ""

if [ "$failed" -ne 0 ]; then
    echo "❌ Development environment is NOT ready (see logs above)."
    exit 1
fi

echo "🎉 Development environment is ready!"
echo "📝 Useful commands:"
echo "   make logs           - View all logs"
echo "   make logs-chainlit  - View Chainlit logs"
echo "   make logs-web       - View web logs"
echo "   make status         - Check container status"
echo "   make restart        - Restart web service"
echo ""
echo "🌍 Access the application:"
echo "   Web: http://localhost:8000"
echo "   Chainlit: http://localhost:8500"
echo ""
echo "🔧 Environment: DEVELOPMENT"
echo "   API URL: http://localhost:8000/api"
echo "   Chainlit URL: http://localhost:8500"
