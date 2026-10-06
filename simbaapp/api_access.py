"""
Who may call SIMBA's JSON API (/api/...).

The API used to answer anyone on the internet: e.g. GET /api/dashboard/raw_messages/ returned every student's
conversations without logging in, and the admin and dashboard endpoints trusted a user id sent in the URL.
ApiAccessMiddleware now checks every /api/ request first:

  - SIMBA's own server-side calls (the pages in views.py and the chat service) send INTERNAL_HEADER with a key
    derived from SECRET_KEY, which both already have; they are trusted, because they act for a user they have
    already checked themselves.
  - Login, registration, email verification and password reset stay open: they are how people get in.
  - The chat service's own endpoints (next-session, session/<id>, init-session, threads get-or-create, messages)
    are internal only: no browser ever calls them.
  - Everything else needs a logged-in user (the Django session the pages already use), and:
      * every user id sent as a parameter (user_id, current_user_id, requesting_user_id, or in the URL path)
        must be that user's own, so the endpoints' existing owner checks apply to the real user;
      * /api/admin/... needs an admin;
      * dashboard and course-participant data need someone who teaches the course (owner, teacher or admin);
        "all courses" is narrowed to the courses the user teaches inside the endpoints (messages_taught_by).
"""
import hashlib
import hmac
import json
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse

from .models import Activity, Course, CourseEnrollment, Message, User

INTERNAL_HEADER = 'X-Simba-Internal-Key'

PUBLIC_PATHS = {
    '/api/auth/register', '/api/auth/login', '/api/auth/verify-email', '/api/auth/resend-verification',
    '/api/auth/password-reset-request', '/api/auth/password-reset',
}
INTERNAL_ONLY = [re.compile(p) for p in (
    r'^/api/chainlit/next-session$', r'^/api/chainlit/session/[^/]+$', r'^/api/chainlit/init-session$',
    r'^/api/threads/get-or-create$', r'^/api/threads/[^/]+/messages$',
)]
ADMIN_ONLY = [re.compile(p) for p in (r'^/api/admin/', r'^/api/docs', r'^/api/openapi\.json$')]
# User ids that are part of the URL path and must be the caller's own
OWN_ID_IN_PATH = [re.compile(p) for p in (r'^/api/users/([^/]+)$', r'^/api/threads/user-attempts/[^/]+/([^/]+)$')]
COURSE_IN_PATH = re.compile(r'^/api/courses/([^/]+)/participants$')
IDENTITY_PARAMS = ('user_id', 'current_user_id', 'requesting_user_id')


def internal_api_key():
    """The key SIMBA's own server-side calls send; derived from SECRET_KEY, shared by the web and chat containers."""
    return hmac.new(settings.SECRET_KEY.encode(), b'simba-internal-api', hashlib.sha256).hexdigest()


def internal_headers():
    """Headers for server-side calls to SIMBA's own API (requests/httpx)."""
    return {INTERNAL_HEADER: internal_api_key()}


def is_internal(request):
    sent = request.headers.get(INTERNAL_HEADER, '')
    return bool(sent) and hmac.compare_digest(sent, internal_api_key())


def session_user(request):
    """The logged-in user of this request (Django session), or None."""
    if not hasattr(request, '_simba_session_user'):
        user_id = request.session.get('user_id')
        request._simba_session_user = _by_id(User.objects, user_id) if user_id else None
    return request._simba_session_user


def teaches(user, course):
    """Owner of the course, enrolled in it as teacher, or admin."""
    return (user.is_admin or course.owner_id == user.id
            or CourseEnrollment.objects.filter(user=user, course=course, role='teacher').exists())


def courses_taught_by(user):
    """Courses whose data this user may see; None means all of them (admin)."""
    if user.is_admin:
        return None
    return Course.objects.filter(Q(owner=user) | Q(enrollments__user=user, enrollments__role='teacher')).distinct()


def messages_taught_by(request):
    """
    The messages the caller may analyse: all of them for an admin or an internal call, otherwise only those in
    courses the logged-in user teaches. Dashboard endpoints start from this instead of Message.objects.all().
    """
    user = session_user(request)
    if user is None:
        return Message.objects.all() if is_internal(request) else Message.objects.none()
    courses = courses_taught_by(user)
    return Message.objects.all() if courses is None else Message.objects.filter(thread__activity__course__in=courses)


def _by_id(queryset, value):
    """The object with this id, or None (also for a malformed id, instead of a server error)."""
    try:
        return queryset.filter(id=value).first()
    except (ValueError, ValidationError):
        return None


def _deny(status, message):
    return JsonResponse({'message': message}, status=status)


def _json_body(request):
    if 'application/json' not in request.content_type or not request.body:
        return {}
    try:
        body = json.loads(request.body)
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


class ApiAccessMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/api/'):
            denied = self.check(request)
            if denied is not None:
                return denied
        return self.get_response(request)

    def check(self, request):
        path = request.path.rstrip('/') or '/'
        if is_internal(request):
            return None
        if path in PUBLIC_PATHS:
            return None
        if any(p.match(path) for p in INTERNAL_ONLY):
            return _deny(403, 'This endpoint is only for SIMBA itself.')

        user = session_user(request)
        if user is None:
            return _deny(401, 'Please log in.')
        own_id = str(user.id)

        sent_ids = [request.GET.get(p) for p in IDENTITY_PARAMS] + [_json_body(request).get('user_id')]
        for pattern in OWN_ID_IN_PATH:
            match = pattern.match(path)
            if match:
                sent_ids.append(match.group(1))
        if any(i and str(i) != own_id for i in sent_ids):
            return _deny(403, 'You can only act as yourself.')

        if any(p.match(path) for p in ADMIN_ONLY) and not user.is_admin:
            return _deny(403, 'Admin access required.')

        course_ids = []
        if path.startswith('/api/dashboard/'):
            course_ids.append(request.GET.get('course_id'))
            activity_id = request.GET.get('activity_id')
            if activity_id and activity_id != 'all':
                activity = _by_id(Activity.objects.select_related('course'), activity_id)
                if activity is None:
                    return _deny(404, 'Activity not found.')
                if not teaches(user, activity.course):
                    return _deny(403, 'Only teachers of this course can see its data.')
        match = COURSE_IN_PATH.match(path)
        if match:
            course_ids.append(match.group(1))
        for course_id in course_ids:
            if course_id and course_id != 'all':
                course = _by_id(Course.objects, course_id)
                if course is None:
                    return _deny(404, 'Course not found.')
                if not teaches(user, course):
                    return _deny(403, 'Only teachers of this course can see its data.')
        return None
