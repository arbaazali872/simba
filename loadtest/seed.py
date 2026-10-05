"""
Creates the load-test data in the test database and prints it as JSON:
one teacher, one course, one activity and N enrolled students, all with the same password.
Run inside the load-test web container (see run.sh), never on a real database:
    docker exec -i -w /code lt-web python - 200 < loadtest/seed.py > loadtest/data.json
Safe to run again: existing test users are kept, missing ones are added.
"""
import json
import os
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'simba.settings')
import django
django.setup()
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from simbaapp.models import User, Course, CourseEnrollment, Activity

count = int(sys.argv[1]) if len(sys.argv) > 1 else 200
password = 'loadtest-password'
# One hash for everyone: hashing takes ~2 s per call, so 500 separate hashes would take ~20 minutes
password_hash = make_password(password)
now = timezone.now()

teacher, _ = User.objects.get_or_create(username='lt-teacher', defaults={
    'email': 'lt-teacher@loadtest.invalid', 'password_hash': password_hash,
    'is_email_verified': True, 'email_verified_at': now})
course, _ = Course.objects.get_or_create(title='Load test course', owner=teacher)
activity, _ = Activity.objects.get_or_create(title='Load test activity', course=course, owner=teacher, defaults={
    'description': 'Activity used by the load test', 'ai_model': 'together'})

names = [f'lt-student{i:04d}' for i in range(1, count + 1)]
existing = set(User.objects.filter(username__in=names).values_list('username', flat=True))
User.objects.bulk_create([
    User(username=name, email=f'{name}@loadtest.invalid', password_hash=password_hash,
         is_email_verified=True, email_verified_at=now)
    for name in names if name not in existing])

students = {u.username: str(u.id) for u in User.objects.filter(username__in=names)}
enrolled = set(CourseEnrollment.objects.filter(course=course).values_list('user_id', flat=True))
CourseEnrollment.objects.bulk_create([
    CourseEnrollment(user_id=uid, course=course, role='student')
    for uid in students.values() if uid not in {str(e) for e in enrolled}])

print(json.dumps({
    'password': password,
    'course_id': str(course.id),
    'activity_id': str(activity.id),
    'students': students,
}))
