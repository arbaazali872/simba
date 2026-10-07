"""
Who may call SIMBA's API (the addresses under /api/).

Step 1: SIMBA's own server-side calls carry an "internal key".

Two parts of SIMBA call SIMBA's own API from the server, not from someone's browser:
  - the pages in views.py: e.g. the registration page or the admin pages don't do the work themselves, they send
    a request to /api/... in the background (with the requests library);
  - the chat program (chainlit_app.py), which runs in its own container and reads and saves chat messages through
    /api/...
Neither has a logged-in user's cookie. When the API starts refusing callers who are not logged in, these calls
must be able to prove they come from SIMBA itself. They do it by sending the header INTERNAL_HEADER with the key
below.

The key is calculated from Django's SECRET_KEY (with HMAC, a standard way to derive a new secret from an existing
one). The web and chat containers both read SECRET_KEY from the same .env, so both can calculate the same key and
there is nothing new to configure. The key is only sent from SIMBA's server to SIMBA's server; browsers never
receive it.

Step 2: the API refuses callers who are not logged in.

Before, the API answered anyone on the internet, logged in or not: e.g. GET /api/dashboard/raw_messages/ returned
every student's conversations to a visitor without an account (confirmed on the live site on Oct 5, 2026).
ApiAccessMiddleware (switched on in simba/settings.py) now looks at every request to /api/ BEFORE any API code runs,
and lets it through only if:
  1. it carries SIMBA's internal key (step 1): the pages' and the chat program's own calls;
  2. it is one of the ways in (PUBLIC_PATHS: login, registration, email verification, password reset), which by
     nature are used before logging in;
  3. someone is logged in: the same login the pages use (request.session['user_id']), which the browser sends with
     every request automatically.
Anything else gets 401 "Please log in." Pages (/login/, /courses/, ...) are not affected: only /api/ is checked.
For people using SIMBA normally nothing changes: they are logged in, and the pages and the chat carry the key.
What this step does NOT do yet: a logged-in account can still reach everything (later steps).

Step 3: the chat program's own addresses are closed to browsers.

Some API addresses exist only for the chat program (chainlit_app.py), see CHAT_PROGRAM_ONLY: collecting the chat
session a student just started, finding or creating the student's conversation, and loading and saving its
messages. No page ever calls them (checked: no JavaScript in the templates uses them); the chat program calls
them from the server, with the internal key. Before, any browser could use them too: read any conversation and
write messages into it given its number, or take chat sessions waiting for other students.
Now a request to these addresses WITHOUT the internal key is refused (403), even from a logged-in user.
Nothing changes for people using SIMBA: their chat window goes through the chat program, which has the key.

Step 4: you can only act as yourself.

Many API addresses are told WHO is acting through a parameter: e.g. deleting a course is
DELETE /api/courses/<course>?user_id=<who is deleting>, and the API then checks "is <who is deleting> the owner?".
The API believed whatever id it was sent, so a logged-in student could send a teacher's id and act as that
teacher: delete their course, change their activities, start a chat in their name, use admin functions with an
admin's id. Now every id that says "who is acting" (ACTING_USER_PARAMS, ACTING_USER_IN_PATH, and "user_id" in the
request body) must be the logged-in user's own; otherwise the request is refused (403).
The API's existing owner checks ("only the course owner can delete it") then apply to the real user, without
changing those addresses. Ids that say who something is ABOUT (student_id, target_user_id: the student a teacher
looks at, the user an admin edits) are not checked here; who may see or change whom is for later steps.
Nothing changes for people using SIMBA: the pages always send the logged-in user's own id.

Step 5: the admin area of the API and the API documentation are for admins only.

/api/admin/... (user lists, statistics, deleting users, courses and activities) is used only by the admin pages,
which already send non-admins away. Each admin address also checks "is this user an admin?" itself, and since
step 4 that user can only be the logged-in one. This step adds a second lock in front of the whole admin area
(ADMIN_ONLY), so an admin address added later without its own check is still protected. It also closes the API's
documentation pages (/api/docs, /api/openapi.json): a complete, clickable map of every API address, which any
logged-in user could open before. Refused with 403 for anyone who is not an admin.
Nothing changes for people using SIMBA: only admins use the admin pages, and nobody's work needs the docs.
"""
import hashlib
import hmac
import json
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse

from .models import User

INTERNAL_HEADER = 'X-Simba-Internal-Key'

# The ways in: they must work before anyone is logged in. Every other /api/ address needs a login (or the key).
PUBLIC_PATHS = {
    '/api/auth/register',
    '/api/auth/login',
    '/api/auth/verify-email',
    '/api/auth/resend-verification',
    '/api/auth/password-reset-request',
    '/api/auth/password-reset',
}

# Used only by the chat program (chainlit_app.py), never by a page: refused without the internal key.
# Written as patterns because some contain a number in the middle ([^/]+ = "any one path segment").
CHAT_PROGRAM_ONLY = [re.compile(pattern) for pattern in (
    r'^/api/chainlit/next-session$',         # take the oldest waiting chat session (fallback for old pages)
    r'^/api/chainlit/session/[^/]+$',        # take a chat session by its number
    r'^/api/chainlit/init-session$',         # older way to start a session (no longer used by anything)
    r'^/api/threads/get-or-create$',         # find or create a student's conversation
    r'^/api/threads/[^/]+/messages$',        # read or save the messages of a conversation
)]

# Admins only (step 5): the admin area of the API and the API documentation
ADMIN_ONLY = [re.compile(pattern) for pattern in (
    r'^/api/admin/',                         # everything under /api/admin/
    r'^/api/docs',                           # the clickable API documentation page
    r'^/api/openapi\.json$',                 # the same documentation, as data
)]

# Parameters that say WHO IS ACTING; they must be the logged-in user's own id (step 4).
ACTING_USER_PARAMS = (
    'user_id',               # courses, activities, attempts, files, visibility, all admin functions
    'current_user_id',       # removing a student from a course
    'requesting_user_id',    # dashboard: who is asking for a student's data or the statistics
)
# The same, when the id is part of the address itself
ACTING_USER_IN_PATH = [re.compile(pattern) for pattern in (
    r'^/api/users/([^/]+)$',                          # changing your profile
    r'^/api/threads/user-attempts/[^/]+/([^/]+)$',    # your attempts at an activity
)]


def internal_api_key():
    """The key SIMBA's own server-side calls send. Same value in the web and chat containers (same SECRET_KEY)."""
    return hmac.new(settings.SECRET_KEY.encode(), b'simba-internal-api', hashlib.sha256).hexdigest()


def internal_headers():
    """Headers to add to SIMBA's own calls to its API, e.g. requests.post(url, headers=internal_headers(), ...)."""
    return {INTERNAL_HEADER: internal_api_key()}


def is_internal(request):
    """True if this request carries the right internal key, i.e. it comes from SIMBA itself."""
    sent = request.headers.get(INTERNAL_HEADER, '')
    # compare_digest takes the same time whether the first or the last character differs,
    # so the key cannot be guessed character by character from response times
    return bool(sent) and hmac.compare_digest(sent, internal_api_key())


def session_user(request):
    """
    The user logged in on this request, or None. Uses the same login as the pages: at login, views.login_view
    stores the user's id in request.session['user_id'].
    """
    if not hasattr(request, '_simba_session_user'):   # look it up once per request
        user_id = request.session.get('user_id')
        request._simba_session_user = _by_id(User.objects, user_id) if user_id else None
    return request._simba_session_user


def _by_id(queryset, value):
    """The object with this id, or None; also None for a malformed id, instead of a server error."""
    try:
        return queryset.filter(id=value).first()
    except (ValueError, ValidationError):
        return None


def _acting_user_ids(request, path):
    """Every id in this request that says who is acting: in the parameters, in the address, or in the body."""
    ids = [request.GET.get(name) for name in ACTING_USER_PARAMS]
    for pattern in ACTING_USER_IN_PATH:
        match = pattern.match(path)
        if match:
            ids.append(match.group(1))
    # Starting a chat sends {"activity_id": ..., "user_id": <for whom>, ...} in the body
    if 'application/json' in request.content_type and request.body:
        try:
            body = json.loads(request.body)
        except ValueError:
            body = None
        if isinstance(body, dict):
            ids.append(body.get('user_id'))
    return [str(i).lower() for i in ids if i]


def _deny(status, message):
    """The refusal sent back, in the same JSON shape as the API's own error messages."""
    return JsonResponse({'message': message}, status=status)


class ApiAccessMiddleware:
    """
    Checks every request to /api/ before any API code runs (see the explanation at the top of this file).
    Django calls __call__ for every request, pages included; only /api/ addresses are checked.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/api/'):
            refusal = self.check(request)
            if refusal is not None:
                return refusal                      # the API code never runs
        return self.get_response(request)          # carry on as before

    def check(self, request):
        """None = let the request through; otherwise the refusal to send back."""
        path = request.path.rstrip('/')            # '/api/auth/login/' and '/api/auth/login' are the same address
        if is_internal(request):                   # 1. SIMBA itself (pages' and chat program's own calls)
            return None
        if path in PUBLIC_PATHS:                   # 2. a way in
            return None
        if any(p.match(path) for p in CHAT_PROGRAM_ONLY):
            # the chat program's addresses, but without the key (step 1 let the chat program through already)
            return _deny(403, 'This address is only for SIMBA itself.')
        user = session_user(request)
        if user is None:                           # 3. nobody logged in
            return _deny(401, 'Please log in.')
        own_id = str(user.id).lower()
        if any(acting_id != own_id for acting_id in _acting_user_ids(request, path)):
            return _deny(403, 'You can only act as yourself.')   # 4. an id that is not the logged-in user's
        if any(p.match(path) for p in ADMIN_ONLY) and not user.is_admin:
            return _deny(403, 'Admin access required.')          # 5. admin area or docs, not an admin
        return None
