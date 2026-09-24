from django.db import models
import uuid
from datetime import timedelta
from django.utils import timezone


class User(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    username = models.CharField(max_length=255, unique=True, null=False)
    email = models.EmailField(unique=True, null=False)
    password_hash = models.CharField(max_length=255, null=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_login = models.DateTimeField(blank=True, null=True)
    is_email_verified = models.BooleanField(default=False)
    email_verified_at = models.DateTimeField(blank=True, null=True)
    is_admin = models.BooleanField(default=False)

    def __str__(self):
        return self.username
    
    def get_owned_courses_count(self):
        """Get the number of courses this user owns (as teacher)"""
        return Course.objects.filter(owner=self).count()
    
    def get_total_activities_count(self):
        """Get the total number of activities this user has created"""
        return Activity.objects.filter(owner=self).count()
    
    def get_enrolled_courses_count(self):
        """Get the number of courses this user is enrolled in (as student)"""
        return CourseEnrollment.objects.filter(user=self).count()
    
    def can_create_course(self):
        """Check if user can create a new course (limit: 3)"""
        return self.get_owned_courses_count() < 3
    
    def can_create_activity(self, course=None):
        """Check if user can create a new activity (limit: 10 total) and has access to at least one course"""
        total_activities_count = Activity.objects.filter(owner=self).count()
        if total_activities_count >= 10:
            return False
        
        owned_courses = Course.objects.filter(owner=self).exists()
        teacher_enrollments = CourseEnrollment.objects.filter(user=self, role='teacher').exists()
        
        return owned_courses or teacher_enrollments
    
    def can_join_course(self):
        """Check if user can join a new course (limit: 3 total including owned)"""
        total_courses = self.get_owned_courses_count()
        return total_courses < 3

class Course(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="courses")
    created_at = models.DateTimeField(auto_now_add=True)
    enrollment_code = models.CharField(max_length=8, unique=True)

    def __str__(self):
        return self.title
        
    def save(self, *args, **kwargs):
        if not self.enrollment_code:
            import uuid
            self.enrollment_code = uuid.uuid4().hex[:8].upper()
        super().save(*args, **kwargs)


class CourseEnrollment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="enrollments")
    role = models.CharField(max_length=20, choices=[('student', 'Student'), ('teacher', 'Teacher')], default='student')
    joined_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        unique_together = (("user", "course"),)
        
    def __str__(self):
        return f"{self.user} as {self.role} in {self.course}"


class Activity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="activities")
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=255, blank=True, null=True)
    description = models.TextField(blank=True, null=True)
    expert_mode = models.BooleanField(default=False)
    custom_prompt = models.TextField(blank=True, null=True)
    start_date = models.DateTimeField(blank=True, null=True)
    end_date = models.DateTimeField(blank=True, null=True)
    is_visible = models.BooleanField(default=True)
    allow_redo = models.BooleanField(default=True)
    ai_model = models.CharField(max_length=20, choices=[('gpt', 'GPT'), ('mistral', 'Mistral'), ('together', 'Together AI')], default='gpt')
    llm_model = models.CharField(max_length=100, blank=True, null=True)
    openai_assistant_id = models.CharField(max_length=255, blank=True, null=True)
    vector_store_id = models.CharField(max_length=255, blank=True, null=True)
    options = models.JSONField(blank=True, null=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.title:
            return f"{self.title} by {self.owner}"
        return f"Activity by {self.owner} on {self.course}"
    
    def get_option(self, key, default=None):
        """Helper method to get a value from options dict"""
        if self.options and isinstance(self.options, dict):
            return self.options.get(key, default)
        return default
    
    def set_option(self, key, value):
        """Helper method to set a value in options dict"""
        if not self.options:
            self.options = {}
        self.options[key] = value
    
    def get_all_options(self):
        """Helper method to get all options with defaults"""
        defaults = {
            'questions': [],
            'agent_attitude': 'friendly',
            'subjects': '',
            'restrict_to_subject': False,
            'allow_questions': True,
            'never_answer_directly': True,
            'allow_emojis': True,
            'trust_document': True,
            'word_limit': 0
        }
        
        if self.options and isinstance(self.options, dict):
            return {**defaults, **self.options}
        return defaults


class Thread(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="threads")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    attempt_number = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = (("activity", "user", "attempt_number"),)

    def __str__(self):
        return f"Thread by {self.user} on {self.activity} (Attempt {self.attempt_number})"


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content = models.TextField(null=False)
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=50, null=False)
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(blank=True, null=True)
    message_number = models.PositiveIntegerField()

    class Meta:
        ordering = ['message_number']

    def __str__(self):
        return f"Message ({self.role}) at {self.timestamp}"


class Analytics(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity = models.ForeignKey(Activity, on_delete=models.SET_NULL, null=True, blank=True)  
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    course = models.ForeignKey(Course, on_delete=models.SET_NULL, null=True, blank=True)
    metrics = models.JSONField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Analytics at {self.timestamp}"

class Event(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    verb = models.SmallIntegerField(choices = [(0,"Created"), (1,"Deleted"), (2,"Opened"), (3,"Closed"), (4,"Joined"), (5,"Modified")])
    object = models.SmallIntegerField(choices = [(0,"Account"), (1,"Course"), (2,"Activity"), (3,"Thread"), (4, "Message"), (5,"Simba")])
    context = models.JSONField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"subject : {self.user}, verb : {self.verb}, object : {self.object}"


class ChainlitSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session_id = models.CharField(max_length=255, unique=True)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE)
    username = models.CharField(max_length=255)
    session_data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_consumed = models.BooleanField(default=False)

    def __str__(self):
        return f"Chainlit Session {self.session_id} for {self.user.username}"

    class Meta:
        indexes = [
            models.Index(fields=['session_id']),
            models.Index(fields=['expires_at']),
            models.Index(fields=['is_consumed']),
        ]


class EmailVerificationToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="email_verification_tokens")
    token = models.CharField(max_length=32, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    
    def save(self, *args, **kwargs):
        if not self.token:
            self.token = uuid.uuid4().hex
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=24)
        super().save(*args, **kwargs)
    
    def is_valid(self):
        return not self.is_used and timezone.now() < self.expires_at
    
    def __str__(self):
        return f"Email verification token for {self.user.email}"


class PasswordResetToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token = models.CharField(max_length=32, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    
    def save(self, *args, **kwargs):
        if not self.token:
            self.token = uuid.uuid4().hex
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=1)
        super().save(*args, **kwargs)
    
    def is_valid(self):
        return not self.is_used and timezone.now() < self.expires_at
    
    def __str__(self):
        return f"Password reset token for {self.user.email}"


class InviteToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ROLE_CHOICES = [
        ('student', 'Student'),
        ('teacher', 'Teacher'),
    ]
    
    token = models.CharField(max_length=32, unique=True)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="invite_tokens")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_invites")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    
    def save(self, *args, **kwargs):
        if not self.token:
            self.token = uuid.uuid4().hex
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=1)
        super().save(*args, **kwargs)
    
    def is_valid(self):
        return self.is_active and timezone.now() < self.expires_at
    
    def __str__(self):
        return f"Invite to {self.course.title} as {self.role}"


class ActivityToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    token = models.CharField(max_length=32, unique=True)
    activity = models.ForeignKey(Activity, on_delete=models.CASCADE, related_name="activity_tokens")
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_activity_tokens")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    
    def save(self, *args, **kwargs):
        if not self.token:
            self.token = uuid.uuid4().hex
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(days=7)
        super().save(*args, **kwargs)
    
    def is_valid(self):
        return self.is_active and timezone.now() < self.expires_at
    
    def __str__(self):
        return f"Activity link to {self.activity.title} in {self.activity.course.title}"