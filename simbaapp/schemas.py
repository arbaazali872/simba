from ninja import ModelSchema, Schema
from pydantic import EmailStr, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from .models import (
    User,
    Course,
    Activity,
    Message,
    Thread,
    Analytics
)

# --- Authentication Schemas ---

class SignInSchema(Schema):
    username: str
    password: str

class UserRegisterSchema(Schema):
    username: str
    email: EmailStr
    password: str
    password_confirm: str

class UserUpdateSchema(Schema):
    username: str
    email: EmailStr
    current_password: str
    new_password: Optional[str] = None
    new_password_confirm: Optional[str] = None

class PasswordResetRequestSchema(Schema):
    email: EmailStr

class PasswordResetSchema(Schema):
    token: str
    new_password: str
    new_password_confirm: str

class EmailVerificationSchema(Schema):
    token: str

class ResendVerificationSchema(Schema):
    email: EmailStr

class AdminCreateUserSchema(Schema):
    username: str
    email: EmailStr
    password: str
    is_admin: bool = False

# --- Chainlit Session Schemas ---

class ChainlitSessionInitSchema(Schema):
    activity_id: str
    user_id: str
    username: str
    thread_id: Optional[str] = None

class ChainlitSessionResponseSchema(Schema):
    session_id: str
    activity_id: str
    user_id: str
    username: str
    thread_id: str
    language: str
    activity_data: Dict[str, Any]

# --- Input Schemas ---

class CourseCreateSchema(Schema):
    title: str
    description: Optional[str] = None

class CourseUpdateSchema(Schema):
    title: str
    description: Optional[str] = None

class CourseEnrollmentSchema(Schema):
    enrollment_code: str
    role: str = 'student' 

class ActivityCreateSchema(Schema):
    course_id: str 
    title: Optional[str] = None
    description: Optional[str] = None
    expert_mode: bool = False
    custom_prompt: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    is_visible: bool = True
    allow_redo: bool = True
    ai_model: str = 'gpt'
    llm_model: Optional[str] = None
    files: List[str] = Field(default_factory=list)
    options: Optional[Dict[str, Any]] = None
    # For backward compatibility, also accept individual fields
    questions: Optional[List[str]] = Field(default_factory=list)
    agent_attitude: Optional[str] = 'friendly'
    subjects: Optional[str] = None
    restrict_to_subject: Optional[bool] = False
    allow_questions: Optional[bool] = True
    never_answer_directly: Optional[bool] = True
    allow_emojis: Optional[bool] = True
    trust_document: Optional[bool] = True
    word_limit: Optional[int] = 0

class ActivityUpdateSchema(Schema):
    title: Optional[str] = None
    description: Optional[str] = None
    expert_mode: Optional[bool] = False
    custom_prompt: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    is_visible: Optional[bool] = True
    allow_redo: Optional[bool] = True
    ai_model: Optional[str] = 'gpt'
    llm_model: Optional[str] = None
    files: Optional[List[str]] = Field(default_factory=list)
    options: Optional[Dict[str, Any]] = None
    questions: Optional[List[str]] = Field(default_factory=list)
    agent_attitude: Optional[str] = 'friendly'
    subjects: Optional[str] = None
    restrict_to_subject: Optional[bool] = False
    allow_questions: Optional[bool] = True
    never_answer_directly: Optional[bool] = True
    allow_emojis: Optional[bool] = True
    trust_document: Optional[bool] = True
    word_limit: Optional[int] = 0

class ThreadGetOrCreateSchema(Schema):
    activity_id: str
    user_id: str
    attempt_number: Optional[int] = 1

class MessageCreateSchema(Schema):
    thread_id: str
    content: str
    role: str
    user_id: str
    username: Optional[str] = None 
    model: Optional[str] = None

# --- Output Schemas ---

class UserOutSchema(Schema):
    id: str
    username: str
    email: str

class ErrorSchema(Schema):
    message: str

class CourseOutSchema(ModelSchema):
    class Meta:
        model = Course
        fields = ["id", "title", "description", "owner", "created_at", "enrollment_code"]

class ActivityOutSchema(ModelSchema):
    class Meta:
        model = Activity
        fields = "__all__"

class ActivityDetailSchema(Schema):
    id: str
    title: Optional[str]
    description: Optional[str]
    expert_mode: bool
    custom_prompt: Optional[str]
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    is_visible: bool
    allow_redo: bool
    ai_model: str
    llm_model: Optional[str] = None
    options: Optional[Dict[str, Any]] = None
    questions: Optional[List[str]] = None
    agent_attitude: Optional[str] = None
    subjects: Optional[str] = None
    restrict_to_subject: Optional[bool] = None
    allow_questions: Optional[bool] = None
    never_answer_directly: Optional[bool] = None
    allow_emojis: Optional[bool] = None
    trust_document: Optional[bool] = None
    word_limit: Optional[int] = None

    @staticmethod
    def from_activity(activity):
        """Create schema instance from Activity model with options expansion"""
        all_options = activity.get_all_options()
        
        return {
            'id': str(activity.id),
            'title': activity.title,
            'description': activity.description,
            'expert_mode': activity.expert_mode,
            'custom_prompt': activity.custom_prompt,
            'start_date': activity.start_date,
            'end_date': activity.end_date,
            'is_visible': activity.is_visible,
            'allow_redo': activity.allow_redo,
            'ai_model': activity.ai_model,
            'llm_model': activity.llm_model,
            'options': activity.options,
            'questions': all_options.get('questions', []),
            'agent_attitude': all_options.get('agent_attitude', 'friendly'),
            'subjects': all_options.get('subjects', ''),
            'restrict_to_subject': all_options.get('restrict_to_subject', False),
            'allow_questions': all_options.get('allow_questions', True),
            'never_answer_directly': all_options.get('never_answer_directly', True),
            'allow_emojis': all_options.get('allow_emojis', True),
            'trust_document': all_options.get('trust_document', True),
            'word_limit': all_options.get('word_limit', 0)
        }

class ThreadSchema(ModelSchema):
    class Meta:
        model = Thread
        fields = ["id", "activity", "user", "created_at", "updated_at", "attempt_number"]

class MessageSchema(ModelSchema):
    class Meta:
        model = Message
        fields = ["id", "content", "role", "timestamp", "metadata", "message_number"]
    
    metadata: Optional[Dict[str, Any]] = None 
    timestamp: datetime

# --- Model Schemas ---

class UserSchema(ModelSchema):
    class Meta:
        model = User
        fields = "__all__"

class CourseSchema(ModelSchema):
    class Meta:
        model = Course
        fields = ["id", "title", "description", "owner", "created_at", "enrollment_code"]

class ActivitySchema(ModelSchema):
    class Meta:
        model = Activity
        fields = "__all__"

class AnalyticsSchema(ModelSchema):
    class Meta:
        model = Analytics
        fields = "__all__"


class StudentDataSchema(Schema):
    activities_count: int
    messages_count: int
    total_chars: int
    messages: list
    length_distribution: dict
    student_length: int
    activity_engagement: dict
    retries_count: int

class ConversationStatsSchema(Schema):
    stats: list

class SummaryResponseSchema(Schema):
    summary: str

class StudentAnalysisSchema(Schema):
    analysis: str

class ClusterResponseSchema(Schema):
    clusters: list
    features: list

class WordFrequencySchema(Schema):
    words: list
    students: list

class RawMessagesSchema(Schema):
    messages: list

class FileUploadSchema(Schema):
    filename: str
    content: str 
    content_type: str

class ActivityFileSchema(Schema):
    id: str
    filename: str
    size: int
    created_at: datetime

class ActivityFilesResponseSchema(Schema):
    files: List[ActivityFileSchema]
