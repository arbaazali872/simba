import os
import django
import django.apps
from django.contrib.auth.hashers import make_password, check_password
from django.utils import timezone
from django.conf import settings
from django.db import models
from .models import User, Course, Activity, Thread, Message, CourseEnrollment, ChainlitSession, InviteToken, ActivityToken, EmailVerificationToken, PasswordResetToken, Event
from ninja import Swagger, Router
from ninja_extra import NinjaExtraAPI
from ninja_jwt.controller import NinjaJWTDefaultController
from typing import List
from django.db.models import Max
from .schemas import (
    SignInSchema,
    MessageSchema,
    UserRegisterSchema, 
    UserOutSchema,      
    UserUpdateSchema,
    ErrorSchema,       
    CourseCreateSchema,
    CourseUpdateSchema,
    CourseOutSchema,
    ActivityCreateSchema,
    ActivityUpdateSchema,
    ActivityOutSchema,
    ThreadGetOrCreateSchema,
    MessageCreateSchema,
    ThreadSchema,
    ActivityDetailSchema,
    StudentDataSchema,
    ConversationStatsSchema,
    SummaryResponseSchema,
    StudentAnalysisSchema,
    WordFrequencySchema,
    RawMessagesSchema,
    FileUploadSchema,
    ActivityFileSchema,
    ActivityFilesResponseSchema,
    ChainlitSessionInitSchema,
    ChainlitSessionResponseSchema,
    ClusterResponseSchema,
    CourseEnrollmentSchema,
    PasswordResetRequestSchema,
    PasswordResetSchema,
    EmailVerificationSchema,
    ResendVerificationSchema,
    AdminCreateUserSchema,
)
from .eventTracking import (
    accountCreated,
    createdActivity,
    closedChat,
    closedCourse,
    createdCourse,
    deletedActivity,
    deletedCourse,
    joinedActivity,
    joinedCourse,
    loggedIn,
    loggedOut,
    modifiedActivity,
    modifiedCourse,
    modifiedProfile,
    openedChat,
    openedCourse,
    sentMessage 
)
from .email_utils import send_email_verification, send_password_reset_email
import time
import logging

from django.shortcuts import get_object_or_404
from http import HTTPStatus
from . import cluster_students
from . import openai_assistant
from .llm_models import resolve_together_model
from .templates import build_system_prompt

# Configure logging
logger = logging.getLogger(__name__)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'simba.settings')
if not django.apps.apps.ready:
    django.setup()

api = NinjaExtraAPI(csrf=False, docs=Swagger(settings={"persistAuthorization": True}))
api.register_controllers(NinjaJWTDefaultController)


@api.post("/auth/register", response={201: UserOutSchema, 400: ErrorSchema, 409: ErrorSchema})
def register_user(request, payload: UserRegisterSchema):
    """
    Register a new user.
    """
    if payload.password != payload.password_confirm:
        return HTTPStatus.BAD_REQUEST, {"message": "Passwords do not match."}
        
    if User.objects.filter(username=payload.username).exists():
        return HTTPStatus.CONFLICT, {"message": "Username already exists."}
        
    if User.objects.filter(email=payload.email).exists():
        return HTTPStatus.CONFLICT, {"message": "Email is already registered."}
        
    try:
        hashed_password = make_password(payload.password)
        user = User.objects.create(
            username=payload.username,
            email=payload.email,
            password_hash=hashed_password,
            is_email_verified=False
        )
        accountCreated(user,time.time())
        
        next_url = request.GET.get('next')
        send_email_verification(user, next_url)
        
        user_data = {
            "id": str(user.id),
            "username": user.username,
            "email": user.email
        }
        return HTTPStatus.CREATED, user_data
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Registration failed: {str(e)}"}

@api.post("/auth/login", response={200: UserOutSchema, 401: ErrorSchema, 404: ErrorSchema, 500:ErrorSchema})
def login_user(request, payload: SignInSchema):
    """
    Authenticate a user and return user details.
    """
    try:
        user = User.objects.get(username=payload.username)
        if check_password(payload.password, user.password_hash):
            if not user.is_email_verified:
                return HTTPStatus.UNAUTHORIZED, {"message": "Please verify your email address before logging in."}
            
            loggedIn(user, time.time())
            
            user_data = {
                "id": str(user.id),
                "username": user.username,
                "email": user.email
            }
            return HTTPStatus.OK, user_data
        else:
            return HTTPStatus.UNAUTHORIZED, {"message": "Invalid credentials."}
    except User.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "User does not exist."}
    except Exception as e:
         return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Login failed: {str(e)}"}

@api.post("/auth/verify-email", response={200: dict, 400: ErrorSchema, 404: ErrorSchema})
def verify_email(request, payload: EmailVerificationSchema):
    """
    Verify user email with token.
    """
    try:
        token = EmailVerificationToken.objects.get(token=payload.token)
        
        if not token.is_valid():
            return HTTPStatus.BAD_REQUEST, {"message": "Invalid or expired verification token."}
        
        user = token.user
        user.is_email_verified = True
        user.email_verified_at = timezone.now()
        user.save()
        
        token.is_used = True
        token.save()
        
        return HTTPStatus.OK, {"message": "Email verified successfully."}
        
    except EmailVerificationToken.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Invalid verification token."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Email verification failed: {str(e)}"}

@api.post("/auth/resend-verification", response={200: dict, 400: ErrorSchema, 404: ErrorSchema})
def resend_verification_email(request, payload: ResendVerificationSchema):
    """
    Resend email verification for a user.
    """
    try:
        user = User.objects.get(email=payload.email)
        
        if user.is_email_verified:
            return HTTPStatus.BAD_REQUEST, {"message": "Email is already verified."}
        
        EmailVerificationToken.objects.filter(user=user, is_used=False).update(is_used=True)
        
        next_url = request.GET.get('next')
        success = send_email_verification(user, next_url)
        
        if success:
            return HTTPStatus.OK, {"message": "Verification email sent."}
        else:
            return HTTPStatus.BAD_REQUEST, {"message": "Failed to send verification email."}
        
    except User.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "User not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Failed to resend verification: {str(e)}"}

@api.post("/auth/password-reset-request", response={200: dict, 404: ErrorSchema, 500: ErrorSchema})
def request_password_reset(request, payload: PasswordResetRequestSchema):
    """
    Request password reset email.
    """
    try:
        user = User.objects.get(email=payload.email)
        
        PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)
        
        success = send_password_reset_email(user)
        
        if success:
            return HTTPStatus.OK, {"message": "Password reset email sent."}
        else:
            return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": "Failed to send password reset email."}
        
    except User.DoesNotExist:
        return HTTPStatus.OK, {"message": "If the email exists, a password reset link has been sent."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Failed to send password reset: {str(e)}"}

@api.post("/auth/password-reset", response={200: dict, 400: ErrorSchema, 404: ErrorSchema})
def reset_password(request, payload: PasswordResetSchema):
    """
    Reset password with token.
    """
    try:
        if payload.new_password != payload.new_password_confirm:
            return HTTPStatus.BAD_REQUEST, {"message": "Passwords do not match."}
        
        token = PasswordResetToken.objects.get(token=payload.token)
        
        if not token.is_valid():
            return HTTPStatus.BAD_REQUEST, {"message": "Invalid or expired reset token."}
        
        user = token.user
        user.password_hash = make_password(payload.new_password)
        user.save()
        
        token.is_used = True
        token.save()
        
        return HTTPStatus.OK, {"message": "Password reset successfully."}
        
    except PasswordResetToken.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Invalid reset token."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Password reset failed: {str(e)}"}

@api.put("/users/{user_id}", response={200: UserOutSchema, 400: ErrorSchema, 401: ErrorSchema, 404: ErrorSchema, 409: ErrorSchema})
def update_user_profile(request, user_id: str, payload: UserUpdateSchema):
    """
    Update a user's profile information.
    The current password is required to make any changes.
    """
    try:
        user = User.objects.get(id=user_id)
        
        if not check_password(payload.current_password, user.password_hash):
            return HTTPStatus.UNAUTHORIZED, {"message": "Current password is incorrect."}
            
        if payload.username != user.username and User.objects.filter(username=payload.username).exists():
            return HTTPStatus.CONFLICT, {"message": "Username already exists."}
            
        if payload.email != user.email and User.objects.filter(email=payload.email).exists():
            return HTTPStatus.CONFLICT, {"message": "Email is already registered."}
        
        if payload.new_password:
            if not payload.new_password_confirm:
                return HTTPStatus.BAD_REQUEST, {"message": "Password confirmation is required."}
                
            if payload.new_password != payload.new_password_confirm:
                return HTTPStatus.BAD_REQUEST, {"message": "New passwords do not match."}
                
            user.password_hash = make_password(payload.new_password)
        
        user.username = payload.username
        user.email = payload.email
        user.save()
        modifiedProfile(user,{"username" : user.username, "email" : user.email},time.time())
        
        user_data = {
            "id": str(user.id),
            "username": user.username,
            "email": user.email
        }
        return HTTPStatus.OK, user_data
    except User.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "User not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Profile update failed: {str(e)}"}

# --- Course CRUD ---
@api.post("/courses", response={201: CourseOutSchema, 400: ErrorSchema, 403: ErrorSchema})
def create_course_api(request, payload: CourseCreateSchema, user_id: str):
    """
    Create a new course. user_id is passed for now.
    Ideally, this would come from an authenticated token.
    """
    try:
        user = User.objects.get(id=user_id)
        if not user.can_create_course():
            return HTTPStatus.FORBIDDEN, {"message": "You have reached the maximum number of courses (3)."}
        
        course = Course.objects.create(
            title=payload.title,
            description=payload.description,
            owner=user
        )
        createdCourse(user,course.id,{"title" : payload.title,"description" : payload.description, "owner" : user.id},time.time())
        return HTTPStatus.CREATED, course
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Course creation failed: {str(e)}"}

@api.put("/courses/{course_id}", response={200: CourseOutSchema, 400: ErrorSchema, 403: ErrorSchema, 404: ErrorSchema})
def update_course_api(request, course_id: str, payload: CourseUpdateSchema, user_id: str):
    """
    Update an existing course. Only the owner/teacher can update it.
    """
    try:
        user = User.objects.get(id=user_id)
        course = Course.objects.get(id=course_id)
        
        if course.owner_id != user.id:
            return HTTPStatus.FORBIDDEN, {"message": "Only the course owner can update this course."}
        
        course.title = payload.title
        course.description = payload.description if payload.description else course.description
        course.save()
        modifiedCourse(user,course_id,{"title" : course.title,"description" : course.description, "owner" : user.id},time.time())
        return HTTPStatus.OK, course
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Course.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Course not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Course update failed: {str(e)}"}

@api.delete("/courses/{course_id}", response={204: None, 403: ErrorSchema, 404: ErrorSchema})
def delete_course_api(request, course_id: str, user_id: str):
    """
    Delete a course. Only the owner/teacher can delete it.
    """
    try:
        user = User.objects.get(id=user_id)
        course = Course.objects.get(id=course_id)
        
        if course.owner_id != user.id:
            return HTTPStatus.FORBIDDEN, {"message": "Only the course owner can delete this course."}
        
        course.delete()
        deletedCourse(user,course_id,time.time())
        return HTTPStatus.NO_CONTENT, None
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Course.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Course not found."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Course deletion failed: {str(e)}"}

# --- Activity CRUD ---
@api.post("/activities", response={201: ActivityOutSchema, 400: ErrorSchema, 403: ErrorSchema, 404: ErrorSchema})
def create_activity_api(request, payload: ActivityCreateSchema, user_id: str):
    """
    Create a new activity. user_id is passed for now.
    """
    try:
        user = User.objects.get(id=user_id)
        course = Course.objects.get(id=payload.course_id)

        is_owner = course.owner_id == user.id
        enrollment = CourseEnrollment.objects.filter(user=user, course=course).first()
        can_create = is_owner or (enrollment and enrollment.role == 'teacher')
        
        if not can_create:
            return HTTPStatus.FORBIDDEN, {"message": "Only the course owner or teachers can create activities."}
        
        # Check activity limit (10 total activities per user)
        total_activities_count = Activity.objects.filter(owner=user).count()
        if total_activities_count >= 10:
            return HTTPStatus.FORBIDDEN, {"message": f"You can only create up to 10 activities total. You currently have {total_activities_count} activities."}

        if payload.options:
            options = payload.options.copy()
        else:
            options = {}
        
        if payload.questions is not None:
            options['questions'] = payload.questions
        if payload.agent_attitude is not None:
            options['agent_attitude'] = payload.agent_attitude
        if payload.subjects is not None:
            options['subjects'] = payload.subjects
        if payload.restrict_to_subject is not None:
            options['restrict_to_subject'] = payload.restrict_to_subject
        if payload.allow_questions is not None:
            options['allow_questions'] = payload.allow_questions
        if payload.never_answer_directly is not None:
            options['never_answer_directly'] = payload.never_answer_directly
        if payload.allow_emojis is not None:
            options['allow_emojis'] = payload.allow_emojis
        if payload.trust_document is not None:
            options['trust_document'] = payload.trust_document
        if payload.word_limit is not None:
            options['word_limit'] = payload.word_limit
        

        assistant_id = None
        vector_store_id = None

        activity = Activity.objects.create(
            course=course,
            owner=user,
            title=payload.title if payload.title else f"Activity for {course.title}",
            description=payload.description,
            expert_mode=payload.expert_mode,
            custom_prompt=payload.custom_prompt,
            start_date=payload.start_date,
            end_date=payload.end_date,
            is_visible=payload.is_visible,
            allow_redo=payload.allow_redo,
            ai_model=payload.ai_model,
            llm_model=resolve_together_model(payload.llm_model) if payload.ai_model == 'together' else None,
            openai_assistant_id=assistant_id,
            vector_store_id=vector_store_id,
            options=options
        )

        activity_data = {
            'title': payload.title or f"Activity for {course.title}",
            'description': payload.description or '',
            'course_title': course.title,
            'expert_mode': payload.expert_mode,
            'custom_prompt': payload.custom_prompt,
            'questions': options.get('questions', []),
            'agent_attitude': options.get('agent_attitude', 'friendly'),
            'subjects': options.get('subjects', ''),
            'restrict_to_subject': options.get('restrict_to_subject', False),
            'allow_questions': options.get('allow_questions', True),
            'never_answer_directly': options.get('never_answer_directly', True),
            'allow_emojis': options.get('allow_emojis', True),
            'trust_document': options.get('trust_document', True),
            'word_limit': options.get('word_limit', 0),
            'start_date': payload.start_date,
            'end_date': payload.end_date
        }

        user_language = options.get("language","en")

        if not(payload.custom_prompt) or payload.custom_prompt == "":
            custom_prompt = build_system_prompt(activity_data, logger, user_language)
        else :
            custom_prompt = payload.custom_prompt

        activity.custom_prompt = custom_prompt
        activity_data['custom_prompt'] = custom_prompt
        
        if payload.ai_model == 'gpt':
            
            files_to_upload = []
            if payload.files:
                logger.info(f"Processing {len(payload.files)} files for GPT activity")
                for i, file_base64 in enumerate(payload.files):
                    try:
                        filename, content_type, content = file_base64.split(':', 2)
                        files_to_upload.append({
                            'filename': filename,
                            'content_type': content_type,
                            'content': content
                        })
                        logger.info(f"File {i+1}: {filename} ({content_type})")
                    except ValueError:
                        logger.error(f"Invalid file format at index {i}: {file_base64[:100]}...")
                        return HTTPStatus.BAD_REQUEST, {"message": f"Invalid file format at position {i+1}. Expected 'filename:content_type:base64_content'"}
            
            logger.info(f"Creating OpenAI assistant for activity with {len(files_to_upload)} files")
            assistant_result = openai_assistant.create_assistant(activity_data, files_to_upload, user_language)
            
            if not assistant_result['success']:
                error_msg = assistant_result.get('error', 'Unknown error')
                logger.error(f"Failed to create OpenAI assistant: {error_msg}")
                return HTTPStatus.BAD_REQUEST, {"message": f"Failed to create OpenAI assistant: {error_msg}"}

            assistant_id = assistant_result['assistant_id']
            vector_store_id = assistant_result['vector_store_id']
            logger.info(f"Successfully created OpenAI assistant: {assistant_id}, vector_store: {vector_store_id}")
        else:
            logger.info(f"Creating activity with {payload.ai_model} model - no OpenAI assistant needed")
        
        activity.save()

        createdActivity(user, activity.id, {
            "course": payload.course_id,
            "owner": user_id,
            "title": activity.title,
            "description": payload.description,
            "expert_mode": payload.expert_mode,
            "custom_prompt": custom_prompt,
            "start_date": payload.start_date,
            "end_date": payload.end_date,
            "is_visible": payload.is_visible,
            "allow_redo": payload.allow_redo,
            "ai_model": payload.ai_model,
            "llm_model": activity.llm_model,
            "options": options
        }, time.time())
        
        return HTTPStatus.CREATED, activity
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Course.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Course not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Activity creation failed: {str(e)}"}

@api.put("/activities/{activity_id}", response={200: ActivityOutSchema, 400: ErrorSchema, 403: ErrorSchema, 404: ErrorSchema})
def update_activity_api(request, activity_id: str, payload: ActivityUpdateSchema, user_id: str):
    """
    Update an existing activity. Only the owner/teacher can update it.
    """
    try:
        user = User.objects.get(id=user_id)
        activity = Activity.objects.get(id=activity_id)
        
        if activity.owner_id != user.id and activity.course.owner_id != user.id:
            return HTTPStatus.FORBIDDEN, {"message": "Only the activity owner or course owner can update this activity."}
        
        previous_ai_model = activity.ai_model
        
        if payload.title is not None:
            activity.title = payload.title
        if payload.description is not None:
            activity.description = payload.description
        if payload.expert_mode is not None:
            activity.expert_mode = payload.expert_mode
        if payload.custom_prompt is not None:
            activity.custom_prompt = payload.custom_prompt
        if payload.start_date is not None:
            activity.start_date = payload.start_date
        if payload.end_date is not None:
            activity.end_date = payload.end_date
        if payload.is_visible is not None:
            activity.is_visible = payload.is_visible
        if payload.allow_redo is not None:
            activity.allow_redo = payload.allow_redo
        if payload.ai_model is not None:
            activity.ai_model = payload.ai_model
        if activity.ai_model == 'together':
            activity.llm_model = resolve_together_model(payload.llm_model or activity.llm_model)
        else:
            activity.llm_model = None

        current_options = activity.get_all_options()
        
        if payload.options:
            updated_options = payload.options.copy()
        else:
            updated_options = current_options.copy()
        
        if payload.questions is not None:
            updated_options['questions'] = payload.questions
        if payload.agent_attitude is not None:
            updated_options['agent_attitude'] = payload.agent_attitude
        if payload.subjects is not None:
            updated_options['subjects'] = payload.subjects
        if payload.restrict_to_subject is not None:
            updated_options['restrict_to_subject'] = payload.restrict_to_subject
        if payload.allow_questions is not None:
            updated_options['allow_questions'] = payload.allow_questions
        if payload.never_answer_directly is not None:
            updated_options['never_answer_directly'] = payload.never_answer_directly
        if payload.allow_emojis is not None:
            updated_options['allow_emojis'] = payload.allow_emojis
        if payload.trust_document is not None:
            updated_options['trust_document'] = payload.trust_document
        if payload.word_limit is not None:
            updated_options['word_limit'] = payload.word_limit

        activity.options = updated_options

        activity_data = {
            'title': activity.title or f"Activity for {activity.course.title}",
            'description': activity.description or '',
            'course_title': activity.course.title,
            'expert_mode': activity.expert_mode,
            'custom_prompt': activity.custom_prompt,
            'questions': updated_options.get('questions', []),
            'agent_attitude': updated_options.get('agent_attitude', 'friendly'),
            'subjects': updated_options.get('subjects', ''),
            'restrict_to_subject': updated_options.get('restrict_to_subject', False),
            'allow_questions': updated_options.get('allow_questions', True),
            'never_answer_directly': updated_options.get('never_answer_directly', True),
            'allow_emojis': updated_options.get('allow_emojis', True),
            'trust_document': updated_options.get('trust_document', True),
            'word_limit': updated_options.get('word_limit', 0),
            'start_date': activity.start_date,
            'end_date': activity.end_date
        }

        user_language = updated_options.get("language","en")

        if not(payload.custom_prompt) or payload.custom_prompt == "":
            custom_prompt = build_system_prompt(activity_data, logger, user_language)
        else :
            custom_prompt = payload.custom_prompt

        activity.custom_prompt = custom_prompt
        activity_data["custom_prompt"] = custom_prompt
        
        if activity.ai_model == 'gpt':
        
            files_to_upload = []
            if payload.files:
                for file_base64 in payload.files:
                    try:
                        filename, content_type, content = file_base64.split(':', 2)
                        files_to_upload.append({
                            'filename': filename,
                            'content_type': content_type,
                            'content': content
                        })
                    except ValueError:
                        return HTTPStatus.BAD_REQUEST, {"message": "Invalid file format. Expected 'filename:content_type:base64_content'"}
            
            if activity.openai_assistant_id:
                assistant_result = openai_assistant.update_assistant(
                    activity.openai_assistant_id, 
                    activity_data, 
                    files_to_upload
                )
            else:
                assistant_result = openai_assistant.create_assistant(activity_data, files_to_upload, user_language)
            
            if not assistant_result['success']:
                return HTTPStatus.BAD_REQUEST, {"message": f"Failed to update OpenAI assistant: {assistant_result.get('error', 'Unknown error')}"}
            
            activity.openai_assistant_id = assistant_result['assistant_id']
            activity.vector_store_id = assistant_result['vector_store_id']
            
        elif previous_ai_model == 'gpt' and activity.ai_model != 'gpt':
            if activity.openai_assistant_id:
                try:
                    openai_assistant.delete_assistant(activity.openai_assistant_id, activity.vector_store_id)
                except Exception as e:
                    logger.warning(f"Failed to clean up OpenAI assistant {activity.openai_assistant_id}: {e}")
                
                activity.openai_assistant_id = None
                activity.vector_store_id = None
        
        activity.save()
        
        modifiedActivity(user, activity_id, {
            "title": activity.title,
            "description": activity.description, 
            "owner": user.id, 
            "ai_model": activity.ai_model,
            "llm_model": activity.llm_model,
            "options": updated_options
        }, time.time())
        
        return HTTPStatus.OK, activity
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Activity update failed: {str(e)}"}

@api.delete("/activities/{activity_id}", response={204: None, 403: ErrorSchema, 404: ErrorSchema})
def delete_activity_api(request, activity_id: str, user_id: str):
    """
    Delete an activity. Only the owner/teacher can delete it.
    """
    try:
        user = User.objects.get(id=user_id)
        activity = Activity.objects.get(id=activity_id)
        
        if activity.owner_id != user.id and activity.course.owner_id != user.id:
            return HTTPStatus.FORBIDDEN, {"message": "Only the activity owner or course owner can delete this activity."}
        
        activity.delete()
        deletedActivity(user,activity_id,time.time())
        return HTTPStatus.NO_CONTENT, None
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Activity deletion failed: {str(e)}"}

@api.get("/activities/{activity_id}", response={200: ActivityDetailSchema, 404: ErrorSchema})
def get_activity_api(request, activity_id: str):
    """
    Get a specific activity by ID.
    """
    try:
        activity = Activity.objects.get(id=activity_id)
        activity_data = ActivityDetailSchema.from_activity(activity)
        return HTTPStatus.OK, activity_data
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}

# --- Thread and Message Endpoints for Chainlit --- 

thread_router = Router() 

@thread_router.post("/get-or-create", response={200: ThreadSchema, 201: ThreadSchema, 400: ErrorSchema, 404: ErrorSchema})
def get_or_create_thread_api(request, payload: ThreadGetOrCreateSchema):
    """Get an existing thread or create a new one."""
    try:
        activity = Activity.objects.get(id=payload.activity_id)
        
        latest_thread = Thread.objects.filter(
            activity=activity,
            user_id=payload.user_id
        ).order_by('-attempt_number').first()
        
        attempt_number = payload.attempt_number or 1
        
        if payload.attempt_number is None and latest_thread:
            attempt_number = latest_thread.attempt_number
        
        thread, created = Thread.objects.get_or_create(
            activity=activity,
            user_id=payload.user_id,
            attempt_number=attempt_number
        )
        
        status_code = HTTPStatus.CREATED if created else HTTPStatus.OK
        if not created:
             thread.save(update_fields=['updated_at'])

        user = User.objects.get(id=payload.user_id)
        openedChat(user,thread.id,time.time())
             
        return status_code, thread
        
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Thread operation failed: {str(e)}"}

@thread_router.post("/new-attempt", response={201: dict, 400: ErrorSchema, 404: ErrorSchema})
def create_new_attempt_api(request, activity_id: str, user_id: str):
    """
    Create a new attempt for an activity.
    """
    try:
        user = User.objects.get(id=user_id)
        activity = Activity.objects.get(id=activity_id)
        
        # Check if the activity allows redo
        if not activity.allow_redo:
            return HTTPStatus.BAD_REQUEST, {"message": "This activity does not allow multiple attempts."}
        
        # Get the highest attempt number for this user and activity
        max_attempt = Thread.objects.filter(
            activity=activity,
            user=user
        ).aggregate(Max('attempt_number'))['attempt_number__max']
        
        new_attempt_number = (max_attempt or 0) + 1
        
        # Create a new thread for this attempt
        thread = Thread.objects.create(
            activity=activity,
            user=user,
            attempt_number=new_attempt_number
        )
        
        return HTTPStatus.CREATED, {
            "id": str(thread.id),
            "thread_id": str(thread.id),
            "attempt_number": new_attempt_number,
            "message": f"New attempt #{new_attempt_number} created successfully."
        }
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Failed to create new attempt: {str(e)}"}

@thread_router.get("/user-attempts/{activity_id}/{user_id}", response={200: dict, 400: ErrorSchema, 404: ErrorSchema})
def get_user_attempts_api(request, activity_id: str, user_id: str):
    """
    Get all attempts for a user in a specific activity.
    """
    try:
        user = User.objects.get(id=user_id)
        activity = Activity.objects.get(id=activity_id)
        
        threads = Thread.objects.filter(
            activity=activity,
            user=user
        ).order_by('-attempt_number')
        
        attempts = []
        for thread in threads:
            message_count = Message.objects.filter(thread=thread).count()
            attempts.append({
                "thread_id": str(thread.id),
                "attempt_number": thread.attempt_number,
                "created_at": thread.created_at,
                "updated_at": thread.updated_at,
                "message_count": message_count
            })
        
        return HTTPStatus.OK, {
            "activity_id": str(activity_id),
            "user_id": str(user_id),
            "attempts": attempts,
            "total_attempts": len(attempts)
        }
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Failed to get attempts: {str(e)}"}

@thread_router.get("/{thread_id}/messages", response={200: List[MessageSchema], 404: ErrorSchema})
def get_messages_api(request, thread_id: str):
    """Get all messages for a specific thread."""
    try:
        thread = Thread.objects.get(id=thread_id)
        messages = Message.objects.filter(thread=thread).order_by('message_number')
        return HTTPStatus.OK, list(messages)
    except Thread.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Thread not found."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Failed to get messages: {str(e)}"}

@thread_router.post("/{thread_id}/messages", response={201: MessageSchema, 400: ErrorSchema, 404: ErrorSchema})
def create_message_api(request, thread_id: str, payload: MessageCreateSchema):
    """Create a new message within a thread."""
    try:
        thread = Thread.objects.get(id=thread_id)
        user = User.objects.get(id=payload.user_id)

        last_num = Message.objects.filter(thread=thread).aggregate(Max('message_number')).get('message_number__max') or 0
        
        metadata = {}
        if payload.role == 'user':
             metadata = {"user_id": str(payload.user_id), "author": payload.username}
        elif payload.role == 'assistant':
             metadata = {"model": payload.model, "user_id": str(payload.user_id)}
        else:
             metadata = {"user_id": str(payload.user_id)}
             
        message = Message.objects.create(
            thread=thread,
            content=payload.content,
            role=payload.role,
            message_number=last_num + 1,
            metadata=metadata
        )

        sentMessage(user, message.id, payload.content, time.time())
        return HTTPStatus.CREATED, message
    except Thread.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Thread not found."}
    except User.DoesNotExist:
         return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Message creation failed: {str(e)}"}

@api.delete("/enrollments/{enrollment_id}", response={204: None, 403: ErrorSchema, 404: ErrorSchema})
def remove_student_from_course(request, enrollment_id: str, current_user_id: str):
    """
    Remove a student from a course. Only course owners can do this.
    """
    try:
        current_user = User.objects.get(id=current_user_id)
        enrollment = CourseEnrollment.objects.get(id=enrollment_id)
        
        # Check if the current user is the owner of the course
        if enrollment.course.owner_id != current_user.id:
            return HTTPStatus.FORBIDDEN, {"message": "Only course owners can remove students."}
        
        # Don't allow removing the course owner
        if enrollment.user_id == enrollment.course.owner_id:
            return HTTPStatus.FORBIDDEN, {"message": "Cannot remove the course owner."}
        
        enrollment.delete()
        return HTTPStatus.NO_CONTENT, None
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except CourseEnrollment.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Enrollment not found."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Failed to remove student: {str(e)}"}

@api.put("/activities/{activity_id}/visibility", response={200: dict, 400: ErrorSchema, 403: ErrorSchema, 404: ErrorSchema})
def toggle_activity_visibility_api(request, activity_id: str, user_id: str, is_visible: bool):
    """
    Toggle activity visibility. Only activity owner or course owner can do this.
    """
    try:
        user = User.objects.get(id=user_id)
        activity = Activity.objects.get(id=activity_id)
        
        # Check if user is the owner of the activity or the course
        if activity.owner_id != user.id and activity.course.owner_id != user.id:
            return HTTPStatus.FORBIDDEN, {"message": "Only the activity owner or course owner can change visibility."}
        
        activity.is_visible = is_visible
        activity.save()
        
        return HTTPStatus.OK, {
            "activity_id": str(activity_id),
            "is_visible": is_visible,
            "message": f"Activity visibility {'enabled' if is_visible else 'disabled'} successfully."
        }
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Failed to toggle visibility: {str(e)}"}

# Add the thread router to the main API
api.add_router("/threads", thread_router, tags=["Threads"])

@api.get("/courses/{course_id}", response={200: CourseOutSchema, 404: ErrorSchema})
def get_course_api(request, course_id: str):
    """
    Get a specific course by ID.
    """
    try:
        course = Course.objects.get(id=course_id)
        return HTTPStatus.OK, course
    except Course.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Course not found."}

@api.get("/courses/{course_id}/participants", response={200: dict, 404: ErrorSchema})
def get_course_participants_api(request, course_id: str):
    """
    Get all participants (students and teachers) in a course.
    """
    try:
        course = Course.objects.get(id=course_id)
        enrollments = CourseEnrollment.objects.filter(course=course).select_related('user')
        
        participants = []
        for enrollment in enrollments:
            participants.append({
                "enrollment_id": str(enrollment.id),
                "user_id": str(enrollment.user.id),
                "username": enrollment.user.username,
                "email": enrollment.user.email,
                "role": enrollment.role,
                "enrolled_at": enrollment.joined_at
            })
        
        return HTTPStatus.OK, {
            "course_id": str(course_id),
            "course_title": course.title,
            "participants": participants,
            "total_participants": len(participants)
        }
    except Course.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Course not found."}


# api = NinjaAPI(auth=GlobalAuth()) # Apply auth globally if needed

# --- Dashboard API Endpoints ---
dashboard_router = Router()


@dashboard_router.get("/student/{student_id}/", response=StudentDataSchema)
def get_student_data(request, student_id: str, course_id: str = "all", activity_id: str = "all", requesting_user_id: str = None):
    """
    Get comprehensive analytics data for a specific student.
    """
    try:
        student = User.objects.get(id=student_id)
        
        # Determine if this is a self-query or teacher querying student
        is_self_query = requesting_user_id is None or requesting_user_id == student_id
        
        if not is_self_query:
            # Validate permissions for teachers viewing student data
            requesting_user = User.objects.get(id=requesting_user_id)
            
            if course_id != "all":
                course = Course.objects.get(id=course_id)
                # Check if requesting user is owner or teacher in this course
                is_owner = course.owner_id == requesting_user_id
                enrollment = CourseEnrollment.objects.filter(user=requesting_user, course=course).first()
                has_permission = is_owner or (enrollment and enrollment.role == 'teacher')
                
                if not has_permission:
                    return {"error": "Permission denied. You can only view data for courses where you are owner or teacher."}
            else:
                # For "all" courses, check if requesting user has teacher/owner role in any course
                owned_courses = Course.objects.filter(owner=requesting_user)
                teacher_enrollments = CourseEnrollment.objects.filter(user=requesting_user, role='teacher')
                has_any_teacher_role = owned_courses.exists() or teacher_enrollments.exists()
                
                if not has_any_teacher_role:
                    return {"error": "Permission denied. You need teacher or owner privileges to view other students' data."}
        
        if course_id != "all":
            course = Course.objects.get(id=course_id)
            student_enrollment = CourseEnrollment.objects.filter(
                user=student, 
                course=course, 
                role='student'
            ).first()
            
            if not student_enrollment:
                return {"error": "Student is not enrolled in this course."}
                
            messages_query = Message.objects.filter(
                thread__user=student, 
                thread__activity__course=course,
                role='user'
            )
            
            threads_query = Thread.objects.filter(
                user=student,
                activity__course=course
            )
            
            courses_for_comparison = [course]
        else:
            student_courses = Course.objects.filter(
                enrollments__user=student,
                enrollments__role='student'
            )
            
            if not student_courses.exists():
                return {"error": "Student is not enrolled in any courses as a student."}
            
            messages_query = Message.objects.filter(
                thread__user=student,
                thread__activity__course__in=student_courses,
                role='user'
            )
            
            threads_query = Thread.objects.filter(
                user=student,
                activity__course__in=student_courses
            )
            
            courses_for_comparison = student_courses
        
        if activity_id != "all":
            try:
                activity = Activity.objects.get(id=activity_id)
                messages_query = messages_query.filter(thread__activity=activity)
                threads_query = threads_query.filter(activity=activity)
            except Activity.DoesNotExist:
                return {"error": "Activity not found"}
        
        messages = messages_query.select_related('thread__activity').order_by('timestamp')
        threads = threads_query.order_by('-updated_at')
        
        activities = set([msg.thread.activity_id for msg in messages])
        activities_count = len(activities)
        
        messages_count = messages.count()
        total_chars = sum([len(msg.content) for msg in messages])
        
        activity_counts = {}
        for msg in messages:
            activity_name = msg.thread.activity.title or f"Activity {msg.thread.activity.id}"
            activity_counts[activity_name] = activity_counts.get(activity_name, 0) + 1
        
        conversation = []
        
        activity_attempts = {}
        for thread in threads:
            activity_id_key = thread.activity_id
            if activity_id_key not in activity_attempts:
                activity_attempts[activity_id_key] = []
            activity_attempts[activity_id_key].append(thread.attempt_number)
        
        retries_count = 0
        for activity_id_key, attempts in activity_attempts.items():
            retries_count += max(len(attempts) - 1, 0)
        
        if threads.count() <= 5:
            for thread in threads:
                thread_messages = Message.objects.filter(thread=thread).order_by('message_number')
                conversation.extend([
                    {
                        "role": msg.role, 
                        "content": msg.content, 
                        "timestamp": msg.timestamp.isoformat(),
                        "thread_id": str(thread.id),
                        "activity_title": thread.activity.title or f"Activity {thread.activity.id}",
                        "message_number": msg.message_number
                    } 
                    for msg in thread_messages
                ])
        else:
            recent_thread = threads.first()
            if recent_thread:
                thread_messages = Message.objects.filter(thread=recent_thread).order_by('message_number')
                conversation = [
                    {
                        "role": msg.role, 
                        "content": msg.content, 
                        "timestamp": msg.timestamp.isoformat(),
                        "thread_id": str(recent_thread.id),
                        "activity_title": recent_thread.activity.title or f"Activity {recent_thread.activity.id}",
                        "message_number": msg.message_number
                    } 
                    for msg in thread_messages
                ]
        
        # For length distribution, use messages from the same courses for comparison
        all_user_messages = Message.objects.filter(
            role='user',
            thread__activity__course__in=courses_for_comparison
        )
            
        message_lengths = [len(msg.content) for msg in all_user_messages]
        student_avg_length = total_chars / messages_count if messages_count > 0 else 0
        
        max_length = max(message_lengths) if message_lengths else 1000
        bins = list(range(0, max_length + 200, 200))
        values = [0] * len(bins)
        
        for length in message_lengths:
            bin_index = min(length // 200, len(bins) - 1)
            values[bin_index] += 1
        
        return {
            "activities_count": activities_count,
            "messages_count": messages_count,
            "total_chars": total_chars,
            "messages": conversation,
            "length_distribution": {
                "bins": bins,
                "values": values
            },
            "student_length": int(student_avg_length),
            "activity_engagement": {
                "labels": list(activity_counts.keys()),
                "values": list(activity_counts.values())
            },
            "retries_count": retries_count
        }
    except User.DoesNotExist:
        return {"error": "Student not found"}
    except Course.DoesNotExist:
        return {"error": "Course not found"}
    except Exception as e:
        return {"error": str(e)}

@dashboard_router.get("/conversation_stats/", response=ConversationStatsSchema)
def get_conversation_stats(request, course_id: str = "all", requesting_user_id: str = None):
    """Get detailed conversation statistics for analysis."""
    try:
        # Validate permissions if requesting_user_id is provided
        if requesting_user_id:
            requesting_user = User.objects.get(id=requesting_user_id)
            
            if course_id != "all":
                course = Course.objects.get(id=course_id)
                # Check if requesting user is owner or teacher in this course
                is_owner = course.owner_id == requesting_user_id
                enrollment = CourseEnrollment.objects.filter(user=requesting_user, course=course).first()
                has_permission = is_owner or (enrollment and enrollment.role == 'teacher')
                
                if not has_permission:
                    return {"stats": [], "error": "Permission denied. You can only view stats for courses where you are owner or teacher."}
                    
                messages = Message.objects.filter(
                    thread__activity__course=course
                ).select_related('thread__user', 'thread__activity')
            else:
                # For "all" courses, only show data from courses where user has teacher/owner privileges
                owned_courses = Course.objects.filter(owner=requesting_user)
                teacher_courses = Course.objects.filter(
                    enrollments__user=requesting_user,
                    enrollments__role='teacher'
                )
                accessible_courses = (owned_courses | teacher_courses).distinct()
                
                if not accessible_courses.exists():
                    return {"stats": [], "error": "No courses found where you have teacher or owner privileges."}
                
                messages = Message.objects.filter(
                    thread__activity__course__in=accessible_courses
                ).select_related('thread__user', 'thread__activity')
        else:
            # No user validation - return all data (for backward compatibility)
            if course_id != "all":
                course = Course.objects.get(id=course_id)
                messages = Message.objects.filter(
                    thread__activity__course=course
                ).select_related('thread__user', 'thread__activity')
            else:
                messages = Message.objects.all().select_related('thread__user', 'thread__activity')
        
        user_stats = {}
        
        for msg in messages:
            user_id = msg.thread.user_id
            username = msg.thread.user.username
            
            if user_id not in user_stats:
                user_stats[user_id] = {
                    "username": username,
                    "total_turns": 0,
                    "user_turns": 0,
                    "model_turns": 0,
                    "total_chars": 0,
                    "activities": set(),
                    "words": set(),
                    "word_lengths": []
                }
            
            user_stats[user_id]["total_turns"] += 1
            
            if msg.role == 'user':
                user_stats[user_id]["user_turns"] += 1
                user_stats[user_id]["total_chars"] += len(msg.content)
                
                user_stats[user_id]["activities"].add(msg.thread.activity_id)
                
                words = msg.content.lower().split()
                user_stats[user_id]["words"].update(words)
                user_stats[user_id]["word_lengths"].extend([len(w) for w in words])
            else:
                user_stats[user_id]["model_turns"] += 1
        
        stats = []
        for user_id, user_data in user_stats.items():
            if user_data["model_turns"] > 0:
                turn_ratio = user_data["user_turns"] / user_data["model_turns"]
            else:
                turn_ratio = user_data["user_turns"]
                
            avg_word_length = (
                sum(user_data["word_lengths"]) / len(user_data["word_lengths"]) 
                if user_data["word_lengths"] else 0
            )
            
            stats.append({
                "username": user_data["username"],
                "total_turns": user_data["total_turns"],
                "user_turns": user_data["user_turns"],
                "model_turns": user_data["model_turns"],
                "turn_ratio": turn_ratio,
                "num_activities": len(user_data["activities"]),
                "vocab_size": len(user_data["words"]),
                "avg_word_length": avg_word_length
            })
        
        return {"stats": stats}
    except User.DoesNotExist:
        return {"stats": [], "error": "Requesting user not found"}
    except Course.DoesNotExist:
        return {"stats": [], "error": "Course not found"}
    except Exception as e:
        return {"stats": [], "error": str(e)}

@dashboard_router.get("/generate_summary/", response=SummaryResponseSchema)
def generate_activity_summary(request, activity_id: str):
    """Generate an AI summary of student conversations in an activity."""
    try:
        from openai import OpenAI
        import os
        
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return {"summary": "OpenAI API key not found. Please set the OPENAI_API_KEY environment variable."}
        
        openai_client = OpenAI(api_key=api_key)
        
        activity = Activity.objects.get(id=activity_id)
        
        messages = Message.objects.filter(
            thread__activity=activity,
            role='user'
        ).order_by('timestamp')
        
        if not messages:
            return {"summary": "No student messages found for this activity."}
        
        message_texts = [msg.content for msg in messages]
        content = "\n".join(message_texts)
        
        if len(content) > 15000:
            content = content[:15000] + "...(truncated)"
        
        prompt = f"""Based on the messages exchanged between students and SIMBA tutor, taking into consideration only the messages sent by the students:

{content}

Please provide a concise (less than 200 words) SUMMARY that:
1. Summarizes the main points discussed by the students in bullet points
2. Identifies the main difficulties or misconceptions presented by the students in bullet points
"""
        
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",  
                messages=[
                    {"role": "system", "content": "You are a teacher assistant analyzing student conversations."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=500
            )
            
            summary = response.choices[0].message.content
            return {"summary": summary}
        except Exception as e:
            print(f"OpenAI API error: {str(e)}")
            return {"summary": f"Error generating summary with OpenAI: {str(e)}"}
        
    except Activity.DoesNotExist:
        return {"summary": "Activity not found."}
    except Exception as e:
        return {"summary": f"Error generating summary: {str(e)}"}

@dashboard_router.get("/generate_student_analysis/", response=StudentAnalysisSchema)
def generate_student_analysis(request, student_id: str, activity_id: str = "all"):
    """Generate an AI analysis of a student's conversation patterns."""
    try:
        from openai import OpenAI
        import os
        
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return {"analysis": "OpenAI API key not found. Please set the OPENAI_API_KEY environment variable."}
        
        openai_client = OpenAI(api_key=api_key)
        
        student = User.objects.get(id=student_id)
        
        if activity_id != "all":
            activity = Activity.objects.get(id=activity_id)
            messages = Message.objects.filter(
                thread__user=student,
                thread__activity=activity,
                role='user'
            ).order_by('timestamp')
            context = f"for activity '{activity.title}'"
        else:
            messages = Message.objects.filter(
                thread__user=student,
                role='user'
            ).order_by('timestamp')
            context = "across all activities"
        
        if not messages:
            return {"analysis": f"No data found for student {student.username} {context}."}
        
        message_texts = [msg.content for msg in messages]
        content = "\n".join(message_texts)
        
        if len(content) > 15000:
            content = content[:15000] + "...(truncated)"
        
        prompt = f"""You are a teacher assistant. Based on the messages exchanged between the student and an online tutor, here are the messages sent by the student {student.username} {context}:

{content}

Please provide a concise (less than 300 words) summary that:
1. Summarizes the main points discussed by the student.
2. Identifies the main difficulties or misconceptions presented by the student.
3. Analyzes the student's communication style, engagement level, and learning patterns.
For each point, give precise examples cited verbatim from the student's messages.
"""
        
        try:
            response = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a teacher assistant analyzing student conversations."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=600
            )
            
            analysis = response.choices[0].message.content
            return {"analysis": analysis}
        except Exception as e:
            print(f"OpenAI API error: {str(e)}")
            return {"analysis": f"Error generating analysis with OpenAI: {str(e)}"}
        
    except User.DoesNotExist:
        return {"analysis": "Student not found."}
    except Activity.DoesNotExist:
        return {"analysis": "Activity not found."}
    except Exception as e:
        return {"analysis": f"Error generating analysis: {str(e)}"}

@dashboard_router.get("/word_frequencies/", response=WordFrequencySchema)
def get_word_frequencies(request, course_id: str = "all", min_word_length: int = 3, max_words: int = 100):
    """Get word frequencies from student messages for word cloud visualization."""
    try:
        if course_id != "all":
            course = Course.objects.get(id=course_id)
            messages = Message.objects.filter(
                thread__activity__course=course
            ).select_related('thread__user', 'thread__activity')
        else:
            messages = Message.objects.all().select_related('thread__user', 'thread__activity')
        
        print(f"DEBUG: Retrieved {messages.count()} messages for word frequency analysis")
        
        message_data = []
        for msg in messages:
            message_data.append({
                'user_id': str(msg.thread.user_id),
                'username': msg.thread.user.username,
                'content': msg.content,
                'role': msg.role,
                'activity_id': str(msg.thread.activity_id),
                'timestamp': msg.timestamp.isoformat()
            })
        
        word_frequencies = cluster_students.extract_word_frequencies(
            message_data,
            min_word_length=min_word_length,
            max_words=max_words
        )
        
        print(f"DEBUG: Extracted {len(word_frequencies['words'])} words with frequencies")
        
        return word_frequencies
    except Exception as e:
        print(f"ERROR in word_frequencies: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "words": [],
            "students": [],
            "error": str(e)
        }

@dashboard_router.get("/student_clusters/", response=ClusterResponseSchema)
def get_student_clusters(request, course_id: str = "all", n_clusters: int = 3):
    """Generate student clusters and features for scatter plot visualization."""
    try:
        if course_id != "all":
            course = Course.objects.get(id=course_id)
            messages = Message.objects.filter(
                thread__activity__course=course,
                role='user'
            ).select_related('thread__user', 'thread__activity')
        else:
            messages = Message.objects.filter(
                role='user'
            ).select_related('thread__user', 'thread__activity')
        
        print(f"DEBUG: Retrieved {messages.count()} user messages for clustering")
        
        if messages.count() < 2:
            return {
                "clusters": [],
                "features": [],
                "error": "Not enough messages for clustering. Need at least 2 user messages."
            }
        
        message_data = []
        for msg in messages:
            message_data.append({
                'user_id': str(msg.thread.user_id),
                'username': msg.thread.user.username,
                'content': msg.content,
                'role': msg.role,
                'activity_id': str(msg.thread.activity_id),
                'timestamp': msg.timestamp.isoformat()
            })
        
        result = cluster_students.cluster_students(
            message_data,
            n_clusters=n_clusters
        )
        
        print(f"DEBUG: Generated {len(result.get('clusters', []))} clusters with {len(result.get('features', []))} feature points")
        
        return result
    except Exception as e:
        print(f"ERROR in student_clusters: {str(e)}")
        import traceback
        traceback.print_exc()
        return {
            "clusters": [],
            "features": [],
            "error": str(e)
        }

@dashboard_router.get("/raw_messages/", response=RawMessagesSchema)
def get_raw_messages(request, course_id: str = "all", activity_id: str = "all"):
    """Get raw message data for export, with complete message content instead of truncated content."""
    try:
        if course_id != "all":
            course = Course.objects.get(id=course_id)
            messages_query = Message.objects.filter(
                thread__activity__course=course
            ).select_related('thread__user', 'thread__activity', 'thread__activity__course').order_by('-timestamp')
        else:
            messages_query = Message.objects.all().select_related('thread__user', 'thread__activity', 'thread__activity__course').order_by('-timestamp')
        
        if activity_id != "all":
            activity = Activity.objects.get(id=activity_id)
            messages_query = messages_query.filter(thread__activity=activity)
        
        messages = []
        for msg in messages_query:
            messages.append({
                'role': msg.role,
                'content': msg.content,
                'timestamp': msg.timestamp.isoformat(),
                'username': msg.thread.user.username,
                'activity_title': msg.thread.activity.title,
                'course_title': msg.thread.activity.course.title,
                'thread_id': str(msg.thread.id),
                'message_number': msg.message_number
            })
        
        return {"messages": messages}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"messages": [], "error": str(e)}

# --- Activity File Management Endpoints ---
@api.get("/activities/{activity_id}/files", response={200: ActivityFilesResponseSchema, 404: ErrorSchema, 500: ErrorSchema})
def get_activity_files_api(request, activity_id: str):
    """Get list of files for an activity."""
    try:
        activity = Activity.objects.get(id=activity_id)
        
        if not activity.vector_store_id:
            return HTTPStatus.OK, {"files": []}
        
        files = openai_assistant.get_assistant_files(activity.vector_store_id)
        return HTTPStatus.OK, {"files": files}
        
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Failed to get activity files: {str(e)}"}

@api.post("/activities/{activity_id}/files", response={201: dict, 400: ErrorSchema, 403: ErrorSchema, 404: ErrorSchema})
def upload_activity_file_api(request, activity_id: str, payload: FileUploadSchema, user_id: str):
    """Upload a file to an activity."""
    try:
        user = User.objects.get(id=user_id)
        activity = Activity.objects.select_related('course').get(id=activity_id)
        
        # Check permissions
        is_owner = activity.course.owner_id == user.id
        enrollment = CourseEnrollment.objects.filter(user=user, course=activity.course).first()
        can_upload = is_owner or (enrollment and enrollment.role == 'teacher')
        
        if not can_upload:
            return HTTPStatus.FORBIDDEN, {"message": "Only the course owner or teachers can upload files to this activity."}
        
        if not activity.vector_store_id:
            from openai import OpenAI
            client = OpenAI(api_key=os.getenv('OPENAI_API_KEY'))
            vector_store = client.vector_stores.create(
                name=f"Activity: {activity.title or 'Untitled'}"
            )
            activity.vector_store_id = vector_store.id
            activity.save()
        
        file_data = {
            'filename': payload.filename,
            'content_type': payload.content_type,
            'content': payload.content
        }
        
        result = openai_assistant.upload_file_to_assistant(activity.vector_store_id, file_data)
        
        if not result['success']:
            return HTTPStatus.BAD_REQUEST, {"message": f"Failed to upload file: {result.get('error', 'Unknown error')}"}
        
        return HTTPStatus.CREATED, {
            "message": "File uploaded successfully",
            "file_id": result['file_id'],
            "filename": result['filename'],
            "size": result['size']
        }
        
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"File upload failed: {str(e)}"}

@api.delete("/activities/{activity_id}/files/{file_id}", response={204: None, 403: ErrorSchema, 404: ErrorSchema, 500: ErrorSchema})
def delete_activity_file_api(request, activity_id: str, file_id: str, user_id: str):
    """Delete a file from an activity."""
    try:
        user = User.objects.get(id=user_id)
        activity = Activity.objects.select_related('course').get(id=activity_id)
        
        # Check permissions
        is_owner = activity.course.owner_id == user.id
        enrollment = CourseEnrollment.objects.filter(user=user, course=activity.course).first()
        can_delete = is_owner or (enrollment and enrollment.role == 'teacher')
        
        if not can_delete:
            return HTTPStatus.FORBIDDEN, {"message": "Only the course owner or teachers can delete files from this activity."}
        
        if not activity.vector_store_id:
            return HTTPStatus.NOT_FOUND, {"message": "No files found for this activity."}
        
        result = openai_assistant.delete_assistant_file(activity.vector_store_id, file_id)
        
        if not result['success']:
            return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Failed to delete file: {result.get('error', 'Unknown error')}"}
        
        return HTTPStatus.NO_CONTENT, None
        
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"File deletion failed: {str(e)}"}

# --- Chainlit Session Management ---
chainlit_router = Router()

# Removed global queue - sessions are now handled individually
# PENDING_SESSIONS_QUEUE = []  # Removed this global queue

@chainlit_router.post("/create-session", response={200: ChainlitSessionResponseSchema, 400: ErrorSchema, 404: ErrorSchema})
def create_chainlit_session(request, payload: ChainlitSessionInitSchema):
    """Create a Chainlit session for a specific user/activity/thread combination."""
    try:
        # Validate activity exists
        activity = Activity.objects.get(id=payload.activity_id)
        
        # Validate user exists
        user = User.objects.get(id=payload.user_id)
        
        # Clean up expired sessions
        from datetime import datetime, timedelta
        from django.utils import timezone
        ChainlitSession.objects.filter(expires_at__lt=timezone.now()).delete()
        
        # Get or create thread - ensure thread_id is respected if provided
        if payload.thread_id:
            # Use specific thread if provided
            try:
                thread = Thread.objects.get(id=payload.thread_id, activity=activity, user=user)
            except Thread.DoesNotExist:
                return HTTPStatus.NOT_FOUND, {"message": "Thread not found for this user and activity."}
        else:
            # Get latest thread or create new one (should not happen in normal flow)
            latest_thread = Thread.objects.filter(
                activity=activity,
                user=user
            ).order_by('-attempt_number').first()
            
            if latest_thread:
                thread = latest_thread
            else:
                # Create new thread
                thread = Thread.objects.create(
                    activity=activity,
                    user=user,
                    attempt_number=1
                )
        
        # Clean up any existing session for this exact user/activity/thread combination
        ChainlitSession.objects.filter(
            user=user,
            activity=activity,
            thread=thread
        ).delete()
        
        import uuid
        session_id = str(uuid.uuid4())
        
        all_options = activity.get_all_options()
        
        activity_data = {
            'id': str(activity.id),
            'title': activity.title,
            'description': activity.description,
            'expert_mode': activity.expert_mode,
            'custom_prompt': activity.custom_prompt,
            'questions': all_options.get('questions', []),
            'agent_attitude': all_options.get('agent_attitude', 'friendly'),
            'subjects': all_options.get('subjects', ''),
            'restrict_to_subject': all_options.get('restrict_to_subject', False),
            'allow_questions': all_options.get('allow_questions', True),
            'never_answer_directly': all_options.get('never_answer_directly', True),
            'allow_emojis': all_options.get('allow_emojis', True),
            'trust_document': all_options.get('trust_document', True),
            'word_limit': all_options.get('word_limit', 0),
            'ai_model': activity.ai_model,
            'llm_model': activity.llm_model,
            'openai_assistant_id': activity.openai_assistant_id,
            'vector_store_id': activity.vector_store_id,
            'course': {
                'id': str(activity.course.id),
                'title': activity.course.title
            }
        }
        
        user_language = all_options.get('language', 'en')
        
        session_data = {
            'session_id': session_id,
            'activity_id': str(activity.id),
            'user_id': str(user.id),
            'username': payload.username,
            'thread_id': str(thread.id),
            'language': user_language,
            'activity_data': activity_data
        }
        
        # Store session data in database with expiration (1 hour)
        expires_at = timezone.now() + timedelta(hours=1)
        
        chainlit_session = ChainlitSession.objects.create(
            session_id=session_id,
            activity=activity,
            user=user,
            thread=thread,
            username=payload.username,
            session_data=session_data,
            expires_at=expires_at,
            is_consumed=False
        )
        
        logger.info(f"created session data :{session_data}")

        # Track event
        openedChat(user, thread.id, time.time())
        
        # Return session data
        return HTTPStatus.OK, session_data
        
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except User.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "User not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Failed to create session: {str(e)}"}

@chainlit_router.get("/next-session", response={200: ChainlitSessionResponseSchema, 404: ErrorSchema})
def get_next_chainlit_session(request):
    """Get the oldest pending session from the database for Chainlit to process."""
    try:
        from django.utils import timezone
        
        # Clean up expired sessions first
        ChainlitSession.objects.filter(expires_at__lt=timezone.now()).delete()
        
        # Look for the oldest unexpired and unconsumed session
        valid_session = ChainlitSession.objects.filter(
            expires_at__gt=timezone.now(),
            is_consumed=False
        ).select_related('activity', 'user', 'thread').order_by('created_at').first()
        
        if valid_session:
            # Mark session as consumed to prevent reuse
            valid_session.is_consumed = True
            valid_session.save()
            
            # Return the stored session data
            logger.info(f"sent session data : {valid_session.session_data}")
            return HTTPStatus.OK, valid_session.session_data
        else:
            return HTTPStatus.NOT_FOUND, {"message": "No pending sessions."}
            
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Failed to get next session: {str(e)}"}

@chainlit_router.post("/init-session", response={200: ChainlitSessionResponseSchema, 400: ErrorSchema, 404: ErrorSchema})
def init_chainlit_session(request, payload: ChainlitSessionInitSchema):
    """Initialize a Chainlit session with the provided parameters (legacy method)."""
    try:
        # Validate activity exists
        activity = Activity.objects.get(id=payload.activity_id)
        
        # Validate user exists
        user = User.objects.get(id=payload.user_id)
        
        # Get or create thread
        if payload.thread_id:
            # Use specific thread if provided
            thread = Thread.objects.get(id=payload.thread_id, activity=activity, user=user)
        else:
            # Get latest thread or create new one
            latest_thread = Thread.objects.filter(
                activity=activity,
                user=user
            ).order_by('-attempt_number').first()
            
            if latest_thread:
                thread = latest_thread
            else:
                # Create new thread
                thread = Thread.objects.create(
                    activity=activity,
                    user=user,
                    attempt_number=1
                )
        
        # Generate session ID (could be more sophisticated)
        import uuid
        session_id = str(uuid.uuid4())
        
        # Get all options with defaults
        all_options = activity.get_all_options()
        
        # Prepare activity data for Chainlit using the options field
        activity_data = {
            'id': str(activity.id),
            'title': activity.title,
            'description': activity.description,
            'expert_mode': activity.expert_mode,
            'custom_prompt': activity.custom_prompt,
            'questions': all_options.get('questions', []),
            'agent_attitude': all_options.get('agent_attitude', 'friendly'),
            'subjects': all_options.get('subjects', ''),
            'restrict_to_subject': all_options.get('restrict_to_subject', False),
            'allow_questions': all_options.get('allow_questions', True),
            'never_answer_directly': all_options.get('never_answer_directly', True),
            'allow_emojis': all_options.get('allow_emojis', True),
            'trust_document': all_options.get('trust_document', True),
            'word_limit': all_options.get('word_limit', 0),
            'ai_model': activity.ai_model,
            'llm_model': activity.llm_model,
            'openai_assistant_id': activity.openai_assistant_id,
            'vector_store_id': activity.vector_store_id,
            'course': {
                'id': str(activity.course.id),
                'title': activity.course.title
            }
        }
        
        user_language = all_options.get('language', 'en')
        
        session_data = {
            'session_id': session_id,
            'activity_id': str(activity.id),
            'user_id': str(user.id),
            'username': payload.username,
            'thread_id': str(thread.id),
            'language': user_language,
            'activity_data': activity_data
        }
        
        from datetime import datetime, timedelta
        from django.utils import timezone
        expires_at = timezone.now() + timedelta(hours=1)
        
        ChainlitSession.objects.filter(
            user=user,
            activity=activity,
            expires_at__lt=timezone.now()
        ).delete()
        
        chainlit_session = ChainlitSession.objects.create(
            session_id=session_id,
            activity=activity,
            user=user,
            thread=thread,
            username=payload.username,
            session_data=session_data,
            expires_at=expires_at
        )
        
        # Track event
        openedChat(user, thread.id, time.time())
        
        return HTTPStatus.OK, session_data
        
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except User.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "User not found."}
    except Thread.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Thread not found."}
    except Exception as e:
        return HTTPStatus.BAD_REQUEST, {"message": f"Failed to initialize session: {str(e)}"}

@chainlit_router.get("/session/{session_id}", response={200: ChainlitSessionResponseSchema, 404: ErrorSchema})
def get_chainlit_session(request, session_id: str):
    """Get session data by session ID."""
    try:
        from django.utils import timezone
        
        # Clean up expired sessions
        ChainlitSession.objects.filter(expires_at__lt=timezone.now()).delete()
        
        # Get session
        chainlit_session = ChainlitSession.objects.get(
            session_id=session_id,
            expires_at__gt=timezone.now()
        )
        
        return HTTPStatus.OK, chainlit_session.session_data
        
    except ChainlitSession.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Session not found or expired. Please re-initialize."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Failed to get session: {str(e)}"}

api.add_router("/chainlit", chainlit_router, tags=["Chainlit"])
api.add_router("/dashboard", dashboard_router, tags=["Dashboard"])

@api.post("/courses/{course_id}/invite-tokens", response={201: dict, 403: ErrorSchema, 404: ErrorSchema, 500: ErrorSchema})
def generate_invite_token(request, course_id: str, role: str):
    """
    Generate an invite token for a course. Only course owners can generate tokens.
    """
    try:
        user_id = request.session.get('user_id')
        if not user_id:
            return HTTPStatus.UNAUTHORIZED, {"message": "Authentication required."}
        
        course = Course.objects.select_related('owner').get(id=course_id)
        user = User.objects.get(id=user_id)
        
        # Only course owner can generate invite tokens
        if course.owner.id != user.id:
            return HTTPStatus.FORBIDDEN, {"message": "Only course owners can generate invite tokens."}
        
        # Validate role
        if role not in ['student', 'teacher']:
            return HTTPStatus.BAD_REQUEST, {"message": "Invalid role. Must be 'student' or 'teacher'."}
        
        # Check if there's already a valid token for this course and role
        existing_token = InviteToken.objects.filter(
            course=course, 
            role=role, 
            is_active=True,
            expires_at__gt=timezone.now()
        ).first()
        
        if existing_token:
            # Return the existing valid token instead of creating a new one
            invite_url = f"{settings.BASE_URL}/courses/invite/{existing_token.token}"
            return HTTPStatus.CREATED, {
                "token": existing_token.token,
                "invite_url": invite_url,
                "role": role,
                "expires_at": existing_token.expires_at.isoformat()
            }
        
        # Create new token only if no valid one exists
        invite_token = InviteToken.objects.create(
            course=course,
            role=role,
            created_by=user
        )
        
        # Generate the invite URL
        invite_url = f"{settings.BASE_URL}/courses/invite/{invite_token.token}"
        
        return HTTPStatus.CREATED, {
            "token": invite_token.token,
            "invite_url": invite_url,
            "role": role,
            "expires_at": invite_token.expires_at.isoformat()
        }
        
    except Course.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Course not found."}
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Failed to generate invite token: {str(e)}"}

@api.post("/activities/{activity_id}/activity-tokens", response={201: dict, 403: ErrorSchema, 404: ErrorSchema, 500: ErrorSchema})
def generate_activity_token(request, activity_id: str):
    """
    Generate an activity token for direct access to an activity. 
    Only activity owners or course owners can generate tokens.
    """
    try:
        user_id = request.session.get('user_id')
        if not user_id:
            return HTTPStatus.UNAUTHORIZED, {"message": "Authentication required."}
        
        activity = Activity.objects.select_related('course', 'owner').get(id=activity_id)
        user = User.objects.get(id=user_id)
        
        # Only activity owner or course owner can generate activity tokens
        if activity.owner.id != user.id and activity.course.owner.id != user.id:
            return HTTPStatus.FORBIDDEN, {"message": "Only activity or course owners can generate activity tokens."}
        
        # Check if there's already a valid token for this activity
        existing_token = ActivityToken.objects.filter(
            activity=activity, 
            is_active=True,
            expires_at__gt=timezone.now()
        ).first()
        
        if existing_token:
            # Return the existing valid token instead of creating a new one
            activity_url = f"{settings.BASE_URL}/activities/join/{existing_token.token}"
            return HTTPStatus.CREATED, {
                "token": existing_token.token,
                "activity_url": activity_url,
                "activity_title": activity.title,
                "course_title": activity.course.title,
                "expires_at": existing_token.expires_at.isoformat()
            }
        
        # Create new token only if no valid one exists
        activity_token = ActivityToken.objects.create(
            activity=activity,
            created_by=user
        )
        
        # Generate the activity URL
        activity_url = f"{settings.BASE_URL}/activities/join/{activity_token.token}"
        
        return HTTPStatus.CREATED, {
            "token": activity_token.token,
            "activity_url": activity_url,
            "activity_title": activity.title,
            "course_title": activity.course.title,
            "expires_at": activity_token.expires_at.isoformat()
        }
        
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found."}
    except User.DoesNotExist:
        return HTTPStatus.BAD_REQUEST, {"message": "Invalid user ID."}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": f"Failed to generate activity token: {str(e)}"}

# --- ADMIN SECTION ---
admin_router = Router()

@admin_router.get("/check-access", response={200: dict, 403: ErrorSchema})
def check_admin_access(request, user_id: str):
    """Check if user has admin access"""
    try:
        user = User.objects.get(id=user_id)
        if not user.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        return HTTPStatus.OK, {"is_admin": True}
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid user"}

@admin_router.get("/stats", response={200: dict, 403: ErrorSchema})
def get_admin_stats(request, user_id: str):
    """Get overall system statistics"""
    try:
        user = User.objects.get(id=user_id)
        if not user.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        from django.db.models import Count, Q
        from datetime import datetime, timedelta
        
        # Basic stats
        total_users = User.objects.count()
        total_courses = Course.objects.count()
        total_activities = Activity.objects.count()
        total_messages = Message.objects.count()
        
        # Active users (logged in within last 24 hours)
        active_threshold = timezone.now() - timedelta(hours=24)
        active_users = User.objects.filter(last_login__gte=active_threshold).count()
        
        # Recent events (last 7 days)
        week_ago = timezone.now() - timedelta(days=7)
        recent_events = Event.objects.filter(timestamp__gte=week_ago).count()
        
        # User activity by hour (last 24 hours)
        from django.db.models import DateTimeField
        from django.db.models.functions import TruncHour
        hourly_activity = Event.objects.filter(
            timestamp__gte=active_threshold
        ).annotate(
            hour=TruncHour('timestamp')
        ).values('hour').annotate(
            count=Count('id')
        ).order_by('hour')
        
        return HTTPStatus.OK, {
            "total_users": total_users,
            "total_courses": total_courses,
            "total_activities": total_activities,
            "total_messages": total_messages,
            "active_users": active_users,
            "recent_events": recent_events,
            "hourly_activity": list(hourly_activity)
        }
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid user"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.get("/users", response={200: dict, 403: ErrorSchema})
def get_all_users(request, user_id: str):
    """Get all users with detailed information"""
    try:
        user = User.objects.get(id=user_id)
        if not user.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        users = User.objects.all().order_by('-created_at')
        user_data = []
        
        for u in users:
            courses_owned = Course.objects.filter(owner=u).count()
            courses_enrolled = CourseEnrollment.objects.filter(user=u).count()
            activities_created = Activity.objects.filter(owner=u).count()
            
            user_data.append({
                "id": str(u.id),
                "username": u.username,
                "email": u.email,
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login": u.last_login.isoformat() if u.last_login else None,
                "is_email_verified": u.is_email_verified,
                "is_admin": u.is_admin,
                "courses_owned": courses_owned,
                "courses_enrolled": courses_enrolled,
                "activities_created": activities_created
            })
        
        return HTTPStatus.OK, {"users": user_data}
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid user"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.post("/users", response={201: dict, 400: ErrorSchema, 403: ErrorSchema, 500: ErrorSchema})
def admin_create_user(request, user_id: str, payload: AdminCreateUserSchema):
    """Admin create new user"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        if User.objects.filter(username=payload.username).exists():
            return HTTPStatus.BAD_REQUEST, {"message": "Username already exists"}
        
        if User.objects.filter(email=payload.email).exists():
            return HTTPStatus.BAD_REQUEST, {"message": "Email already exists"}
        
        user = User.objects.create(
            username=payload.username,
            email=payload.email,
            password_hash=make_password(payload.password),
            is_email_verified=True,  # Admin-created users are pre-verified
            email_verified_at=timezone.now(),
            is_admin=payload.is_admin
        )
        
        accountCreated(user, time.time())
        
        return HTTPStatus.CREATED, {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "is_admin": user.is_admin
        }
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid admin user"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.put("/users/{target_user_id}", response={200: dict, 400: ErrorSchema, 403: ErrorSchema, 404: ErrorSchema, 500: ErrorSchema})
def admin_update_user(request, user_id: str, target_user_id: str, username: str = None, email: str = None, 
                     password: str = None, is_email_verified: bool = None, is_admin: bool = None):
    """Admin update user"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        target_user = User.objects.get(id=target_user_id)
        
        if username and username != target_user.username:
            if User.objects.filter(username=username).exists():
                return HTTPStatus.BAD_REQUEST, {"message": "Username already exists"}
            target_user.username = username
        
        if email and email != target_user.email:
            if User.objects.filter(email=email).exists():
                return HTTPStatus.BAD_REQUEST, {"message": "Email already exists"}
            target_user.email = email
        
        if password:
            target_user.password_hash = make_password(password)
        
        if is_email_verified is not None:
            target_user.is_email_verified = is_email_verified
            if is_email_verified and not target_user.email_verified_at:
                target_user.email_verified_at = timezone.now()
        
        if is_admin is not None:
            target_user.is_admin = is_admin
        
        target_user.save()
        
        modifiedProfile(target_user, {
            "username": target_user.username,
            "email": target_user.email,
            "is_admin": target_user.is_admin
        }, time.time())
        
        return HTTPStatus.OK, {
            "id": str(target_user.id),
            "username": target_user.username,
            "email": target_user.email,
            "is_admin": target_user.is_admin
        }
    except User.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "User not found"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.get("/recent-events", response={200: dict, 403: ErrorSchema})
def get_recent_events(request, user_id: str, limit: int = 100):
    """Get recent system events"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        events = Event.objects.all().select_related('user').order_by('-timestamp')[:limit]
        
        event_data = []
        for e in events:
            event_data.append({
                "id": str(e.id),
                "user": e.user.username if e.user else "System",
                "user_id": str(e.user.id) if e.user else None,
                "verb": e.get_verb_display(),
                "object": e.get_object_display(),
                "context": e.context,
                "timestamp": e.timestamp.isoformat()
            })
        
        return HTTPStatus.OK, {"events": event_data}
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid admin user"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.get("/usage-analytics", response={200: dict, 403: ErrorSchema, 500: ErrorSchema})
def get_usage_analytics(request, user_id: str):
    """Get detailed usage analytics"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        from django.db.models import Count, Avg, Sum
        from datetime import datetime, timedelta
        
        # Message statistics
        message_stats = Message.objects.aggregate(
            total_messages=Count('id')
        )
        
        # Activity by day of week
        from django.db.models.functions import ExtractWeekDay
        weekly_activity = Event.objects.annotate(
            weekday=ExtractWeekDay('timestamp')
        ).values('weekday').annotate(
            count=Count('id')
        ).order_by('weekday')
        
        # Most active users
        most_active = Event.objects.values('user__username').annotate(
            event_count=Count('id')
        ).order_by('-event_count')[:10]
        
        # Average session duration (approximation based on events)
        user_sessions = {}
        events = Event.objects.filter(
            timestamp__gte=timezone.now() - timedelta(days=7)
        ).order_by('user', 'timestamp')
        
        for event in events:
            if event.user:
                user_id_str = str(event.user.id)
                if user_id_str not in user_sessions:
                    user_sessions[user_id_str] = []
                user_sessions[user_id_str].append(event.timestamp)
        
        # Calculate average session duration
        session_durations = []
        for user_events in user_sessions.values():
            if len(user_events) > 1:
                duration = (user_events[-1] - user_events[0]).total_seconds() / 60  # in minutes
                if duration < 180:  # Cap at 3 hours for a single session
                    session_durations.append(duration)
        
        avg_session_duration = sum(session_durations) / len(session_durations) if session_durations else 0
        
        return HTTPStatus.OK, {
            "message_stats": message_stats,
            "weekly_activity": list(weekly_activity),
            "most_active_users": list(most_active),
            "avg_session_duration_minutes": round(avg_session_duration, 2),
            "total_sessions_analyzed": len(session_durations)
        }
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid admin user"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.delete("/users/{target_user_id}", response={204: None, 403: ErrorSchema, 404: ErrorSchema, 500: ErrorSchema})
def admin_delete_user(request, user_id: str, target_user_id: str):
    """Admin delete user"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        target_user = User.objects.get(id=target_user_id)
        
        # Don't allow deleting yourself
        if str(admin.id) == str(target_user.id):
            return HTTPStatus.BAD_REQUEST, {"message": "Cannot delete your own admin account"}
        
        target_user.delete()
        return HTTPStatus.NO_CONTENT, None
    except User.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "User not found"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.get("/courses", response={200: dict, 403: ErrorSchema, 500: ErrorSchema})
def admin_get_all_courses(request, user_id: str):
    """Get all courses with detailed information"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        courses = Course.objects.all().select_related('owner').order_by('-created_at')
        course_data = []
        
        for c in courses:
            enrollment_count = CourseEnrollment.objects.filter(course=c).count()
            activity_count = Activity.objects.filter(course=c).count()
            
            course_data.append({
                "id": str(c.id),
                "title": c.title,
                "description": c.description,
                "enrollment_code": c.enrollment_code,
                "owner_id": str(c.owner.id),
                "owner_username": c.owner.username,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "enrollment_count": enrollment_count,
                "activity_count": activity_count
            })
        
        return HTTPStatus.OK, {"courses": course_data}
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid admin user"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.delete("/courses/{course_id}", response={204: None, 403: ErrorSchema, 404: ErrorSchema, 500: ErrorSchema})
def admin_delete_course(request, user_id: str, course_id: str):
    """Admin delete course"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        course = Course.objects.get(id=course_id)
        course.delete()
        deletedCourse(admin, course_id, time.time())
        
        return HTTPStatus.NO_CONTENT, None
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid admin user"}
    except Course.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Course not found"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.get("/online-users", response={200: dict, 403: ErrorSchema, 500: ErrorSchema})
def get_online_users(request, user_id: str):
    """Get currently online users based on recent activity"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        from datetime import datetime, timedelta
        
        # Consider users active if they have events in the last 5 minutes
        cutoff_time = timezone.now() - timedelta(minutes=5)
        
        recent_events = Event.objects.filter(
            timestamp__gte=cutoff_time
        ).select_related('user').order_by('-timestamp')
        
        # Group by user to get latest activity
        user_activity = {}
        for event in recent_events:
            if event.user and str(event.user.id) not in user_activity:
                user_activity[str(event.user.id)] = {
                    "username": event.user.username,
                    "last_action": event.get_verb_display(),
                    "last_object": event.get_object_display(),
                    "timestamp": event.timestamp.isoformat()
                }
        
        online_users = list(user_activity.values())
        
        return HTTPStatus.OK, {
            "online_users": online_users,
            "count": len(online_users),
            "cutoff_minutes": 5
        }
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid admin user"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.get("/activities", response={200: dict, 403: ErrorSchema, 500: ErrorSchema})
def admin_get_all_activities(request, user_id: str):
    """Get all activities with detailed information"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        activities = Activity.objects.all().select_related('owner', 'course').order_by('-created_at')
        activity_data = []
        
        for a in activities:
            thread_count = Thread.objects.filter(activity=a).count()
            
            activity_data.append({
                "id": str(a.id),
                "title": a.title,
                "description": a.description,
                "course_id": str(a.course.id),
                "course_title": a.course.title,
                "owner_id": str(a.owner.id),
                "owner_username": a.owner.username,
                "is_visible": a.is_visible,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "start_date": a.start_date.isoformat() if a.start_date else None,
                "end_date": a.end_date.isoformat() if a.end_date else None,
                "thread_count": thread_count
            })
        
        return HTTPStatus.OK, {"activities": activity_data}
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid admin user"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

@admin_router.delete("/activities/{activity_id}", response={204: None, 403: ErrorSchema, 404: ErrorSchema, 500: ErrorSchema})
def admin_delete_activity(request, user_id: str, activity_id: str):
    """Admin delete activity"""
    try:
        admin = User.objects.get(id=user_id)
        if not admin.is_admin:
            return HTTPStatus.FORBIDDEN, {"message": "Admin access required"}
        
        activity = Activity.objects.get(id=activity_id)
        
        # Clean up OpenAI resources if they exist
        try:
            from simbaapp.openai_assistant import OpenAIAssistant
            if activity.openai_assistant_id:
                openai_assistant = OpenAIAssistant()
                openai_assistant.delete_assistant(activity.openai_assistant_id)
                if activity.vector_store_id:
                    openai_assistant.delete_vector_store(activity.vector_store_id)
        except Exception as cleanup_error:
            # Log the error but don't fail the deletion
            print(f"Warning: Failed to clean up OpenAI resources for activity {activity_id}: {cleanup_error}")
        
        activity.delete()
        deletedActivity(admin, activity_id, time.time())
        
        return HTTPStatus.NO_CONTENT, None
    except User.DoesNotExist:
        return HTTPStatus.FORBIDDEN, {"message": "Invalid admin user"}
    except Activity.DoesNotExist:
        return HTTPStatus.NOT_FOUND, {"message": "Activity not found"}
    except Exception as e:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {"message": str(e)}

# Register admin router
api.add_router("/admin", admin_router)
