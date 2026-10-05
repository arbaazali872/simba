# SIMBA load test

Measures how SIMBA behaves with many students at once, on a throwaway local copy configured like the
production server (2 CPUs, so 4 Gunicorn workers; 3.8 GB RAM in total). **Never run it against production.**

## Run

From the repo root, in Git Bash, with Docker running:

```bash
loadtest/run.sh 500        # starts the copy on http://localhost:8001 and creates 500 test students
```

Then one scenario per run (`-u` students, `-r` how many start per second, `-t` duration):

```bash
mkdir -p loadtest/results
env/Scripts/locust -f loadtest/locustfile.py --host http://localhost:8001 --headless \
    -u 100 -r 100 -t 5m --csv loadtest/results/login_100 LoginBurst
docker logs lt-web 2>&1 | grep -c "WORKER TIMEOUT"     # requests Gunicorn had to kill after 240 s
```

| Scenario | What it simulates | Issue it checks |
|---|---|---|
| `LoginBurst` | a class logging in at the same moment | #30 (login deadlock), #31 (slow password check) |
| `Browse` | logged-in students moving between their pages | general speed |
| `OpenChat` | students opening the chat; fails when one gets another student's session | #43 |
| `SendMessages` | saving chat messages and loading the history, without calling any AI | #32 |

When done:

```bash
loadtest/stop.sh           # removes the containers; the test database was only in memory
```

## What it does not cover

- The AI providers' own speed: no OpenAI, Together or Mistral calls are made (their addresses are blocked).
- The Chainlit websocket itself: `OpenChat` makes the same API calls Chainlit makes, without a real chat window.
- nginx in front of production, and the network between the server and the students.
