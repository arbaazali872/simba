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

This step only ADDS the key to SIMBA's own calls. Nothing checks it yet, so nothing changes for anyone.
"""
import hashlib
import hmac

from django.conf import settings

INTERNAL_HEADER = 'X-Simba-Internal-Key'


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
