"""
SIMBA load test scenarios (Locust). Run against the throwaway copy started by run.sh, never production.

One scenario per run, chosen by its class name, e.g. 100 students logging in at the same moment:
    env/Scripts/locust -f loadtest/locustfile.py --host http://localhost:8001 --headless \
        -u 100 -r 100 -t 5m --csv loadtest/results/login_100 LoginBurst

Scenarios:
    LoginBurst    a class logs in at the same moment                       (#30, #31)
    Browse        logged-in students moving between their pages
    OpenChat      students opening the chat; checks each one gets their own (#43)
    SendMessages  the database side of chatting, without calling any AI    (#32)

Against the real server (LoginBurst only), with dedicated test accounts and a few users at a time:
    SIMBA_ACCOUNTS_CSV=loadtest/prod_test_accounts.csv SIMBA_PASSWORD='...' env/Scripts/locust -f loadtest/locustfile.py \
        --host https://simba-refact.irit.fr --headless -u 3 -r 3 -t 2m --csv loadtest/results/prod_3 LoginBurst
"""
import csv
import itertools
import json
import os
import random
from pathlib import Path

import gevent
from locust import HttpUser, between, constant, task

ACCOUNTS_CSV = os.getenv('SIMBA_ACCOUNTS_CSV')
if ACCOUNTS_CSV:
    # Real server: accounts from a CSV ("email" column; the email is the username), one shared password
    with open(ACCOUNTS_CSV, newline='', encoding='utf-8-sig') as f:
        STUDENTS = [(row['email'].strip(), None) for row in csv.DictReader(f) if row.get('email', '').strip()]
    DATA = {'password': os.environ['SIMBA_PASSWORD']}
else:
    # Local copy started by run.sh
    DATA = json.loads((Path(__file__).parent / 'data.json').read_text())
    STUDENTS = list(DATA['students'].items())
_student_numbers = itertools.count()


class SimbaUser(HttpUser):
    """One test student. Each simulated user gets a different account."""
    abstract = True
    # Longer than Gunicorn's 240 s worker timeout, so a hung request shows up with its real duration
    network_timeout = 300.0
    connection_timeout = 30.0

    def on_start(self):
        self.username, self.user_id = STUDENTS[next(_student_numbers) % len(STUDENTS)]
        if not ACCOUNTS_CSV:
            # The site's pages call its own API at the address in the Host header (request.build_absolute_uri).
            # Inside the local container that address must be localhost:8000, although we connect through 8001.
            self.client.headers['Host'] = 'localhost:8000'

    def login(self):
        self.client.cookies.clear()
        self.client.get('/login/', name='GET /login/')
        # Over HTTPS, Django's CSRF check also wants a Referer from the same site, as a browser sends
        with self.client.post('/login/', name='POST /login/', allow_redirects=False, catch_response=True,
                              headers={'Referer': f'{self.host}/login/'}, data={
            'username': self.username,
            'password': DATA['password'],
            'csrfmiddlewaretoken': self.client.cookies.get('csrftoken', ''),
        }) as response:
            if response.status_code == 302 and '/courses' in response.headers.get('Location', ''):
                response.success()
                return True
            response.failure(f'login failed: HTTP {response.status_code}')
            return False

    def page(self, path, name):
        """A page that needs the student to be logged in; a redirect means the session was lost."""
        with self.client.get(path, name=name, allow_redirects=False, catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f'HTTP {response.status_code}')
            return response


class LoginBurst(SimbaUser):
    """Every student logs in once, all at the start of the run, then waits."""
    wait_time = constant(1)
    done = False

    @task
    def log_in_once(self):
        if not self.done:
            self.done = True
            self.login()


class Browse(SimbaUser):
    """Logged-in students opening their courses and activities pages."""
    wait_time = between(2, 5)

    def on_start(self):
        super().on_start()
        self.login()

    @task(3)
    def courses(self):
        self.page('/courses/', 'GET /courses/')

    @task(2)
    def activities(self):
        self.page('/activities/', 'GET /activities/')

    @task(1)
    def course_detail(self):
        self.page(f"/courses/{DATA['course_id']}/", 'GET /courses/[id]/')


class OpenChat(SimbaUser):
    """
    Students opening the chat, the way the site does it:
      1. the chat page creates a ChainlitSession for this student (views.chainlit_view)
      2. the browser loads the chat, then Chainlit asks for "the next session" (chainlit_app.on_chat_start)
    Step 2 is simulated with the same API call Chainlit makes. A session belonging to another student is
    counted as a failure: that student would see someone else's conversation (#43).
    """
    wait_time = between(5, 15)

    def on_start(self):
        super().on_start()
        self.login()

    @task
    def open_chat(self):
        response = self.page(f"/chainlit/?activity_id={DATA['activity_id']}", 'GET /chainlit/ (create session)')
        if response.status_code != 200:
            return
        # Time for the browser to load the chat window before Chainlit asks for the session
        gevent.sleep(random.uniform(0.5, 3.0))
        with self.client.get('/api/chainlit/next-session', name='GET /api/chainlit/next-session',
                             catch_response=True) as response:
            if response.status_code == 404:
                response.failure('no session waiting: this student would get no chat')
            elif response.status_code != 200:
                response.failure(f'HTTP {response.status_code}')
            elif response.json().get('user_id') != self.user_id:
                response.failure("got another student's session (#43)")
            else:
                response.success()


class SendMessages(SimbaUser):
    """
    What Chainlit does around each AI call (chainlit_app.on_message): save the student's message, load the
    history, save the reply. No AI is called, so this measures SIMBA and its database only.
    """
    wait_time = between(10, 30)

    def on_start(self):
        super().on_start()
        response = self.client.post('/api/threads/get-or-create', name='POST /api/threads/get-or-create',
                                    json={'activity_id': DATA['activity_id'], 'user_id': self.user_id})
        self.thread_id = response.json()['id']

    def save(self, role, content):
        self.client.post(f'/api/threads/{self.thread_id}/messages', name=f'POST /api/threads/[id]/messages ({role})',
                         json={'thread_id': self.thread_id, 'content': content, 'role': role,
                               'user_id': self.user_id, 'username': self.username, 'model': 'loadtest'})

    @task
    def chat_turn(self):
        self.save('user', 'Load test message from a student, about the length of a short question.')
        self.client.get(f'/api/threads/{self.thread_id}/messages', name='GET /api/threads/[id]/messages')
        self.save('assistant', 'Load test reply. ' * 40)
