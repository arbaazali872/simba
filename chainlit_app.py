import os
import django
import django.apps
from openai import AsyncOpenAI, NotFoundError, BadRequestError
from mistralai import Mistral
import chainlit as cl
import logging
import httpx
import asyncio
from chainlit import make_async
import requests
import json
from typing import Dict, Any, Optional
from datetime import datetime
from simbaapp.templates import build_system_prompt, get_first_message
from simbaapp.llm_models import TOGETHER_BASE_URL, DEFAULT_TOGETHER_MODEL, resolve_together_model

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Django API URLs
# https://simba-refact.irit.fr/api for production
# http://localhost:8000/api for development
ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')

if ENVIRONMENT == 'production':
    SIMBA_API_BASE_URL = os.getenv('SIMBA_API_URL_PROD', 'https://simba-refact.irit.fr/api')
else:
    SIMBA_API_BASE_URL = os.getenv('SIMBA_API_URL', 'http://web:8000/api')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'simba.settings')
if not django.apps.apps.ready:
    django.setup()

openai_client = AsyncOpenAI()
mistral_client = Mistral(api_key=os.getenv("MISTRAL_API_KEY"))
# Empty-string fallback stops the OpenAI SDK from sending OPENAI_API_KEY to Together
together_client = AsyncOpenAI(api_key=os.getenv("TOGETHER_API_KEY", ""), base_url=TOGETHER_BASE_URL)

# Default settings for different models
openai_settings = {
    "model": "gpt-4o-mini",
    "temperature": 0.7,
}

mistral_settings = {
    "model": "mistral-medium-latest",
    "temperature": 0.7,
    "max_tokens": 1000,
}

together_settings = {
    "temperature": 0.7,
    # Reasoning models (e.g. gpt-oss) spend part of this budget thinking
    "max_tokens": 4096,
}

async def together_chat(model: str, messages: list):
    """Call Together AI. If the model has been retired, retry once with the default model.
    Returns (response_text, model_used)."""
    try:
        response = await together_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=together_settings["temperature"],
            max_tokens=together_settings["max_tokens"],
        )
    except (NotFoundError, BadRequestError) as e:
        if model == DEFAULT_TOGETHER_MODEL or "model" not in str(e).lower():
            raise
        logger.warning(f"Together model {model} unavailable ({e}), falling back to {DEFAULT_TOGETHER_MODEL}")
        model = DEFAULT_TOGETHER_MODEL
        response = await together_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=together_settings["temperature"],
            max_tokens=together_settings["max_tokens"],
        )
    return response.choices[0].message.content, model

async def api_get_activity(activity_id: str):
    """Get activity data from the API."""
    try:
        response = requests.get(f"{SIMBA_API_BASE_URL}/activities/{activity_id}")
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Failed to get activity: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Error getting activity: {e}")
        return None

async def api_get_or_create_thread(activity_id: str, user_id: str):
    """Get or create a thread for the user and activity."""
    try:
        response = requests.post(f"{SIMBA_API_BASE_URL}/threads/get-or-create", json={
            "activity_id": activity_id,
            "user_id": user_id
        })
        if response.status_code in [200, 201]:
            return response.json()
        else:
            print(f"Failed to get/create thread: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Error getting/creating thread: {e}")
        return None

async def api_create_message(thread_id: str, content: str, role: str, user_id: str, username: str = None, model_name: str = None):
    """Create a message via the API."""
    try:
        payload = {
            "thread_id": thread_id,
            "content": content,
            "role": role,
            "user_id": user_id
        }
        if username:
            payload["username"] = username
        if model_name:
            payload["model"] = model_name
            
        response = requests.post(f"{SIMBA_API_BASE_URL}/threads/{thread_id}/messages", json=payload)
        if response.status_code == 201:
            return response.json()
        else:
            print(f"Failed to create message: {response.status_code} - {response.text}")
            return None
    except Exception as e:
        print(f"Error creating message: {e}")
        return None

async def api_get_messages_for_thread(thread_id: str):
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(f"{SIMBA_API_BASE_URL}/threads/{thread_id}/messages")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"API Error getting messages: {e.response.status_code} - {e.response.text}")
            raise Exception(f"API Error: Could not fetch messages. Status: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"Request Error getting messages: {e}")
            raise Exception(f"Request Error: Could not connect to API for messages.")

async def api_get_next_session():
    """Get the next pending session from the API queue"""
    async with httpx.AsyncClient() as http_client:
        try:
            response = await http_client.get(f"{SIMBA_API_BASE_URL}/chainlit/next-session")
            if response.status_code == 200:
                logger.info(f"Response : {response.json()}")
                return response.json()
            elif response.status_code == 404:
                logger.info("No pending sessions in queue")
                return None
            else:
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"API Error getting next session: {e.response.status_code} - {e.response.text}")
            raise Exception(f"API Error: Could not get next session. Status: {e.response.status_code}")
        except httpx.RequestError as e:
            logger.error(f"Request Error getting next session: {e}")
            raise Exception(f"Request Error: Could not connect to API for next session.")

# def get_language_prompts(language_code: str) -> dict:
#     """Get prompts in different languages"""
#     prompts = {
#         'en': {
#             'intro': "You are a {adj1} {teaching_adj_str} tutor for the course '{courseName}'.",
#             'name_intro': "Your name is SIMBA 😸 (Sistema Inteligente de Medición, Bienestar y Apoyo) and you were created by the Núcleo Milenio de Educación Superior and IRIT Talent team.",
#             'greeting': "Hello! 😸 I am SIMBA, and I will help you reflect on the following questions: ",
#             'help_text': "Help the student answer the following questions:",
#             'respond_style': "Respond in a {adj1}, concise and proactive way",
#         },
#         'fr': {
#             'intro': "Vous êtes un tuteur {adj1} {teaching_adj_str} pour le cours '{courseName}'.",
#             'name_intro': "Votre nom est SIMBA 😸 (Sistema Inteligente de Medición, Bienestar y Apoyo) et vous avez été créé par le Núcleo Milenio de Educación Superior et l'équipe IRIT Talent.",
#             'greeting': "Bonjour ! 😸 Je suis SIMBA, et je vais vous aider à réfléchir sur les questions suivantes : ",
#             'help_text': "Aidez l'étudiant à répondre aux questions suivantes :",
#             'respond_style': "Répondez de manière {adj1}, concise et proactive",
#         },
#         'es': {
#             'intro': "Eres un tutor {adj1} {teaching_adj_str} para el curso '{courseName}'.",
#             'name_intro': "Tu nombre es SIMBA 😸 (Sistema Inteligente de Medición, Bienestar y Apoyo) y fuiste creado por el Núcleo Milenio de Educación Superior y el equipo IRIT Talent.",
#             'greeting': "¡Hola! 😸 Soy SIMBA, y te ayudaré a reflexionar sobre las siguientes preguntas: ",
#             'help_text': "Ayuda al estudiante a responder las siguientes preguntas:",
#             'respond_style': "Responde de manera {adj1}, concisa y proactiva",
#         },
#         'pt': {
#             'intro': "Você é um tutor {adj1} {teaching_adj_str} para o curso '{courseName}'.",
#             'name_intro': "Seu nome é SIMBA 😸 (Sistema Inteligente de Medición, Bienestar y Apoyo) e você foi criado pelo Núcleo Milenio de Educación Superior e equipe IRIT Talent.",
#             'greeting': "Olá! 😸 Eu sou SIMBA, e vou te ajudar a refletir sobre as seguintes questões: ",
#             'help_text': "Ajude o estudante a responder as seguintes questões:",
#             'respond_style': "Responda de forma {adj1}, concisa e proativa",
#         }
#     }
    
#     return prompts.get(language_code, prompts['en'])

# def build_system_prompt(activity_data: dict, logger_instance: logging.Logger, language_code: str = 'en') -> str:
#     adj1 = activity_data.get('agent_attitude', 'friendly')
#     expert_mode = activity_data.get('expert_mode', False)
    
#     activity_title = activity_data.get('title', '')
#     activity_description = activity_data.get('description', '')
    
#     course_info = activity_data.get('course', {})
#     if isinstance(course_info, dict):
#         courseName = course_info.get('title', 'this course')
#     else: 
#         courseName = 'this course'
#         logger_instance.warning(f"Course information might be missing or not in expected format in activity_data for activity {activity_data.get('id')}")

#     allow_emojis_flag = activity_data.get('allow_emojis', True)
#     questions_list = activity_data.get('questions', [])
#     activity_subjects = activity_data.get('subjects', '')
#     restrict_to_subject_flag = activity_data.get('restrict_to_subject', False)
#     trust_document_flag = activity_data.get('trust_document', True)
#     word_limit_val = activity_data.get('word_limit', 0)
#     custom_prompt_text = activity_data.get('custom_prompt', '')
#     allow_bot_to_ask_questions_flag = activity_data.get('allow_questions', True)
#     vector_store_id = activity_data.get('vector_store_id')

#     def emojiGen(useEmojis):
#         return ", using emojis where possible." if useEmojis else "."

#     def questionsGen_str(questions):
#         nstr = ""
#         if questions and isinstance(questions, list):
#             for i, q_item in enumerate(questions):
#                 question_text = q_item if isinstance(q_item, str) else q_item.get('text', '') 
#                 if question_text:
#                     nstr += f"Question {i+1} : {question_text} \n"
#         return nstr.strip()

#     def subjectsGen_str(subjects, restricted):
#         nstr = ""
#         if subjects:
#             nstr += "You should help the student to reflect in depth on the following course subjects :\n <Beginning of the course subjects>\n"
#             nstr += subjects
#             nstr += "\n<end of the course subjects>\n"
#         if restricted:
#             nstr += "You should only speak of those listed subjects. Avoid as much as possible speaking of other subjects, and steer back the student to the course subjects if he tries to deviate from them."
#         return nstr

#     def answersGen_str(is_expert_mode, never_answer_directly):
#         if never_answer_directly:
#             return "You should never give direct answers to the questions. Instead, guide the student to discover the answer through questioning and hints."
#         elif is_expert_mode:
#             return "You should not give the answer, but guide the student to answer."
#         else:
#             return "You can provide an answer to the provided questions if the student asks for it."

#     def teachTypeGen_str(is_expert_mode):
#         return "Act as a Socratic tutor, taking the initiative in getting the students to answer the questions."

#     def teachingAdjGen_str(is_expert_mode):
#         return "socratic" if is_expert_mode else "standard"

#     def docsGen_str(mentiondocuments, has_files):
#         nstr = ""
#         if mentiondocuments and has_files:
#             nstr = "You have access to uploaded documents for this activity. Use these documents to help answer questions and encourage students to reference them when appropriate."
#         elif mentiondocuments and not has_files:
#             nstr = "Encourage them to go and read a section of the provided documents to answer."
#         elif has_files:
#             nstr = "You have access to uploaded documents for this activity that you can reference to help students."
#         return nstr

#     def filesGen_str(has_files):
#         if has_files:
#             return "\n\nIMPORTANT: This activity has uploaded files/documents available. You can search through and reference these documents to provide more accurate and detailed responses. When relevant, cite information from these documents and encourage students to explore them."
#         return ""

#     def limitsGen_str(limit):
#         if limit and limit != 0:
#             return f"Your answers should be {limit} words maximum."
#         return ""

#     def activityContextGen_str(title, description):
#         """Generate activity-specific context for the prompt"""
#         context_str = ""
#         if title and description:
#             context_str = f"This specific activity is titled '{title}' and focuses on: {description}.\n\n"
#         elif title:
#             context_str = f"This specific activity is titled '{title}'.\n\n"
#         elif description:
#             context_str = f"This activity focuses on: {description}.\n\n"
#         return context_str

#     has_files = bool(vector_store_id)

#     emojis_str = emojiGen(allow_emojis_flag)
#     questions_str = questionsGen_str(questions_list)
#     subjects_str = subjectsGen_str(activity_subjects, restrict_to_subject_flag)
#     teaching_adj_str = teachingAdjGen_str(expert_mode)
#     never_answer_directly_flag = activity_data.get('never_answer_directly', True)
#     answers_text = answersGen_str(expert_mode, never_answer_directly_flag)
#     teaching_type_text = teachTypeGen_str(expert_mode)
#     documents_str = docsGen_str(trust_document_flag, has_files)
#     files_str = filesGen_str(has_files)
#     limits_str = limitsGen_str(word_limit_val)
#     activity_context_str = activityContextGen_str(activity_title, activity_description)

#     # Get language-specific prompts
#     lang_prompts = get_language_prompts(language_code)
    
#     full_template = f"""{lang_prompts['intro'].format(adj1=adj1, teaching_adj_str=teaching_adj_str, courseName=courseName)}

# {activity_context_str}{lang_prompts['name_intro']}
# {lang_prompts['respond_style'].format(adj1=adj1)}{emojis_str}

# {lang_prompts['help_text']}

# {questions_str}

# {subjects_str}

# {answers_text} {teaching_type_text}

# {documents_str}

# Your first message should begin with '{lang_prompts['greeting']}' Followed by the questions to answer.

# {limits_str}{files_str}"""
#     system_prompt = full_template.strip()
#     if expert_mode and custom_prompt_text:
#         system_prompt += f"\n\n{custom_prompt_text}"
#     if not allow_bot_to_ask_questions_flag:
#         system_prompt += "\n\nDo not provide questions to the student unless explicitly asked."
#     return system_prompt

@cl.on_chat_start
async def on_chat_start():
    logger.info("Chainlit starting new chat session")
    
    # Try to get the next session from the API queue with retry
    session_data = None
    max_retries = 3
    retry_delay = 0.5  # seconds
    
    for attempt in range(max_retries):
        try:
            session_data = await api_get_next_session()
            
            if session_data:
                break
            else:
                logger.info(f"No pending sessions found on attempt {attempt + 1}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    
        except Exception as e:
            logger.error(f"Error getting session data from API on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
    
    if not session_data:
        logger.warning("No pending sessions found after all retry attempts")
        await cl.Message(content="No chat session is currently available. Please try starting a new chat from the course page.").send()
        return
    
    # Extract session data
    logger.info(f"session data : {session_data}")
    activity_id = session_data['activity_id']
    user_id = session_data['user_id']
    username = session_data['username']
    thread_id = session_data['thread_id']
    activity_data = session_data['activity_data']
    session_id = session_data['session_id']
    language_code = session_data['language']

    logger.info(f"Using session data: session_id={session_id}, activity_id={activity_id}, user_id={user_id}, thread_id={thread_id}, language={language_code}")
    
    # Store in user session
    cl.user_session.set("session_id", session_id)
    cl.user_session.set("activity_id", activity_id)
    cl.user_session.set("user_id", user_id)
    cl.user_session.set("username", username)
    cl.user_session.set("thread_id", thread_id)
    cl.user_session.set("language", language_code)
    cl.user_session.set("activity_data", activity_data)

    try:    
        previous_messages_data = await api_get_messages_for_thread(thread_id)
        logger.info(f"Loaded {len(previous_messages_data) if previous_messages_data else 0} previous messages for thread {thread_id}")
        
        if previous_messages_data:
            for i, msg in enumerate(previous_messages_data[:3]):
                logger.info(f"Message {i+1} (Thread {thread_id}): Role={msg.get('role')}, Content={msg.get('content')[:50]}...")
        
        if not previous_messages_data: 
            # Create the first message
            fixedFirst = True #For when the choice will exist
            
            system_prompt_content = build_system_prompt(activity_data, logger, language_code)
            
            ai_model = activity_data.get('ai_model', 'gpt')
            logger.info(f"Creating initial message using AI model: {ai_model}")
            
            if ai_model == 'mistral':
                if fixedFirst :
                    ai_first_response_content = get_first_message(activity_data, logger, language_code)
                else :
                    mistral_initial_messages = [{"role": "system", "content": system_prompt_content}]
                    
                    response = await mistral_client.chat.complete_async(
                        model=mistral_settings["model"],
                        messages=mistral_initial_messages,
                        temperature=mistral_settings["temperature"],
                        max_tokens=mistral_settings["max_tokens"],
                        stream=False
                    )
                    ai_first_response_content = response.choices[0].message.content
                    
                await api_create_message(thread_id, ai_first_response_content, "assistant", user_id, model_name=mistral_settings["model"])
                await cl.Message(content=ai_first_response_content).send()
                logger.info(f"Created initial Mistral message for new thread {thread_id}")
            elif ai_model == 'together':
                together_model = resolve_together_model(activity_data.get('llm_model'))
                if fixedFirst :
                    ai_first_response_content = get_first_message(activity_data, logger, language_code)
                else :
                    ai_first_response_content, together_model = await together_chat(
                        together_model, [{"role": "system", "content": system_prompt_content}]
                    )

                await api_create_message(thread_id, ai_first_response_content, "assistant", user_id, model_name=together_model)
                await cl.Message(content=ai_first_response_content).send()
                logger.info(f"Created initial Together message ({together_model}) for new thread {thread_id}")
            else:

                # openai_thread_id = cl.user_session.get("openai_thread_id")
                # if not openai_thread_id:
                #     openai_thread = await openai_client.beta.threads.create()
                #     openai_thread_id = openai_thread.id
                #     cl.user_session.set("openai_thread_id", openai_thread_id)
                #     logger.info(f"Created new OpenAI thread: {openai_thread_id}")
                if fixedFirst :
                    ai_first_response_content = get_first_message(activity_data, logger, language_code)
                else :
                    openai_initial_messages = [{"role": "system", "content": system_prompt_content}]
                    
                    response = await openai_client.chat.completions.create(
                        model=openai_settings["model"],
                        messages=openai_initial_messages,
                        temperature=openai_settings["temperature"],
                    )
                    ai_first_response_content = response.choices[0].message.content
                
                await api_create_message(thread_id, ai_first_response_content, "assistant", user_id, model_name=openai_settings["model"])
                await cl.Message(content=ai_first_response_content).send()
                logger.info(f"Created initial OpenAI message for new thread {thread_id}")
            
        else: 
            if previous_messages_data: 
                logger.info(f"Sending {len(previous_messages_data)} messages to Chainlit interface for thread {thread_id}")
                for i, msg_data in enumerate(previous_messages_data):
                    author = msg_data.get('metadata', {}).get('author') if msg_data.get('metadata') else msg_data.get('role')
                    if msg_data['role'] == 'assistant':
                        await cl.Message(content=msg_data['content'], author='Assistant').send()
                        logger.info(f"Sent assistant message {i+1}: {msg_data['content'][:30]}...")
                    else:
                        await cl.Message(content=msg_data['content'], author=author, type="user_message").send() 
                        logger.info(f"Sent user message {i+1}: {msg_data['content'][:30]}...")
                logger.info(f"Finished sending all {len(previous_messages_data)} messages for thread {thread_id}")
        
    except Exception as e:
        logger.error(f"Error during chat start: {e}")
        await cl.Message(content=f"Could not start chat: {str(e)}").send()
        raise

@cl.on_message
async def on_message(message: cl.Message):
    activity_id = cl.user_session.get("activity_id")
    user_id = cl.user_session.get("user_id")
    username = cl.user_session.get("username", "User")
    thread_id = cl.user_session.get("thread_id")
    activity_data = cl.user_session.get("activity_data")
    session_id = cl.user_session.get("session_id")
    
    logger.info(f"Parameters for message - Activity: {activity_id}, User: {user_id}, Thread: {thread_id}, Session: {session_id}")

    if not all([activity_id, user_id, thread_id, activity_data]):
        logger.error(f"Missing required parameters - Activity: {activity_id}, User: {user_id}, Thread: {thread_id}")
        await cl.Message(content="Session error. Please refresh and try again.").send()
        return

    try:
        await api_create_message(thread_id, message.content, "user", user_id, username=username)
        
        ai_model = activity_data.get('ai_model', 'gpt')
        logger.info(f"Using AI model: {ai_model}")
        
        if ai_model == 'mistral':
            try:
                language_code = cl.user_session.get("language", "en")
                system_prompt_content = build_system_prompt(activity_data, logger, language_code)
                
                messages_history_data = await api_get_messages_for_thread(thread_id)
                mistral_messages = [{"role": "system", "content": system_prompt_content}]

                for msg_data in messages_history_data:
                    mistral_role = msg_data['role'] if msg_data['role'] in ["assistant", "user"] else "user" 
                    mistral_messages.append({"role": mistral_role, "content": msg_data['content']})
                    
                response = await mistral_client.chat.complete_async(
                    model=mistral_settings["model"],
                    messages=mistral_messages,
                    temperature=mistral_settings["temperature"],
                    max_tokens=mistral_settings["max_tokens"],
                    stream=False
                )
                ai_response_content = response.choices[0].message.content
                
                await api_create_message(thread_id, ai_response_content, "assistant", user_id, model_name=mistral_settings["model"])
                await cl.Message(content=ai_response_content).send()
                
            except Exception as e:
                logger.error(f"Mistral AI Error: {e}")
                await cl.Message(content=f"I apologize, but I'm having trouble processing your request right now. Please try again in a moment. Error: {str(e)}").send()

        elif ai_model == 'together':
            try:
                together_model = resolve_together_model(activity_data.get('llm_model'))
                language_code = cl.user_session.get("language", "en")
                system_prompt_content = build_system_prompt(activity_data, logger, language_code)

                messages_history_data = await api_get_messages_for_thread(thread_id)
                together_messages = [{"role": "system", "content": system_prompt_content}]

                for msg_data in messages_history_data:
                    together_role = msg_data['role'] if msg_data['role'] in ["assistant", "user"] else "user"
                    together_messages.append({"role": together_role, "content": msg_data['content']})

                ai_response_content, together_model = await together_chat(together_model, together_messages)
                if not ai_response_content:
                    logger.error(f"Together model {together_model} returned an empty response")
                    await cl.Message(content="I apologize, but I couldn't generate a response. Please try again.").send()
                    return

                await api_create_message(thread_id, ai_response_content, "assistant", user_id, model_name=together_model)
                await cl.Message(content=ai_response_content).send()

            except Exception as e:
                logger.error(f"Together AI Error: {e}")
                await cl.Message(content=f"I apologize, but I'm having trouble processing your request right now. Please try again in a moment. Error: {str(e)}").send()
        
        else:
            openai_assistant_id = activity_data.get('openai_assistant_id')
            vector_store_id = activity_data.get('vector_store_id')
            
            logger.info(f"GPT model - Assistant ID: {openai_assistant_id}, Vector Store: {vector_store_id}")
            
            if openai_assistant_id:
                try:
                    logger.info(f"Using OpenAI Assistant API with assistant {openai_assistant_id}")
                    openai_thread_id = cl.user_session.get("openai_thread_id")
                    if not openai_thread_id:
                        openai_thread = await openai_client.beta.threads.create()
                        openai_thread_id = openai_thread.id
                        cl.user_session.set("openai_thread_id", openai_thread_id)
                        logger.info(f"Created new OpenAI thread: {openai_thread_id}")

                        messages_history = await api_get_messages_for_thread(thread_id)
                        if messages_history:
                            logger.info(f"Populating OpenAI thread with {len(messages_history)} existing messages")
                            for msg in messages_history:
                                msg_role = msg['role'] if msg['role'] in ['user', 'assistant'] else 'user'
                                await openai_client.beta.threads.messages.create(
                                    thread_id=openai_thread_id,
                                    role=msg_role,
                                    content=msg['content']
                                )
                            logger.info(f"Successfully populated OpenAI thread with message history")
                    
                    await openai_client.beta.threads.messages.create(
                        thread_id=openai_thread_id,
                        role="user",
                        content=message.content
                    )
                    
                    run = await openai_client.beta.threads.runs.create(
                        thread_id=openai_thread_id,
                        assistant_id=openai_assistant_id
                    )
                    
                    # Wait for completion with timeout
                    max_attempts = 30  # 30 seconds timeout
                    attempts = 0
                    while run.status in ['queued', 'in_progress'] and attempts < max_attempts:
                        await asyncio.sleep(1)
                        attempts += 1
                        run = await openai_client.beta.threads.runs.retrieve(
                            thread_id=openai_thread_id,
                            run_id=run.id
                        )
                        logger.info(f"Run status: {run.status} (attempt {attempts})")
                    
                    if run.status == 'completed':
                        messages = await openai_client.beta.threads.messages.list(
                            thread_id=openai_thread_id,
                            limit=1
                        )
                        
                        if messages.data:
                            latest_message = messages.data[0]
                            if latest_message.content:
                                ai_response_content = latest_message.content[0].text.value
                                
                                await api_create_message(thread_id, ai_response_content, "assistant", user_id, model_name="gpt-4o-mini")
                                
                                await cl.Message(content=ai_response_content).send()
                                logger.info(f"Assistant response sent successfully")
                            else:
                                logger.error("Assistant message has no content")
                                await cl.Message(content="I apologize, but I couldn't generate a response. Please try again.").send()
                        else:
                            logger.error("No messages returned from assistant")
                            await cl.Message(content="I apologize, but I couldn't retrieve the response. Please try again.").send()
                    elif run.status == 'failed':
                        logger.error(f"OpenAI run failed: {run.last_error}")
                        await cl.Message(content="I apologize, but I encountered an error processing your request. Please try again.").send()
                    elif attempts >= max_attempts:
                        logger.error(f"OpenAI run timed out after {max_attempts} seconds")
                        await cl.Message(content="I apologize, but the request is taking too long. Please try again.").send()
                    else:
                        logger.error(f"OpenAI run failed with status: {run.status}")
                        await cl.Message(content="I apologize, but I encountered an error processing your request. Please try again.").send()
                        
                except Exception as e:
                    logger.error(f"OpenAI Assistant API Error: {e}")
                    await cl.Message(content=f"I apologize, but I'm having trouble processing your request right now. Please try again in a moment. Error: {str(e)}").send()
                    
            else:
                logger.info("No OpenAI assistant available - using legacy chat completions mode")
                
                language_code = cl.user_session.get("language", "en")
                system_prompt_content = build_system_prompt(activity_data, logger, language_code)
                
                messages_history_data = await api_get_messages_for_thread(thread_id)
                openai_messages = [{"role": "system", "content": system_prompt_content}]

                for msg_data in messages_history_data:
                    openai_role = msg_data['role'] if msg_data['role'] in ["assistant", "user"] else "user" 
                    openai_messages.append({"role": openai_role, "content": msg_data['content']})
                    
                response = await openai_client.chat.completions.create(
                    model=openai_settings["model"],
                    messages=openai_messages,
                    temperature=openai_settings["temperature"],
                )
                ai_response_content = response.choices[0].message.content
                
                await api_create_message(thread_id, ai_response_content, "assistant", user_id, model_name=openai_settings["model"])
                await cl.Message(content=ai_response_content).send()
                logger.info("Legacy chat completion response sent successfully")
        
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        await cl.Message(content=f"An error occurred: {str(e)}").send()

