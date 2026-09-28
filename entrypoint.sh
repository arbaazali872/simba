#!/bin/sh
set -e

echo "Starting application initialization..."

python manage.py migrate

echo "Checking if default teacher exists..."
python -c "
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'simba.settings')
import django
django.setup()
from django.contrib.auth.hashers import make_password
from simbaapp.models import User, Course, Activity, CourseEnrollment
from django.utils import timezone

# Check if user already exists
if not User.objects.filter(username='prof').exists():
    # Create a new teacher user (without role field)
    password = 'prof'
    password_hash = make_password(password)
    
    teacher = User.objects.create(
        username='prof',
        email='prof@gmail.com',
        password_hash=password_hash,
        is_email_verified=True,
        email_verified_at=timezone.now(),
        is_admin=True
    )
    print('Default teacher user created successfully (email verified, admin privileges)')
else:
    teacher = User.objects.get(username='prof')
    # Ensure existing teacher is verified and admin
    if not teacher.is_email_verified or not teacher.is_admin:
        teacher.is_email_verified = True
        teacher.email_verified_at = timezone.now()
        teacher.is_admin = True
        teacher.save()
        print('Default teacher user verified and granted admin privileges')
    else:
        print('Default teacher user already exists, verified, and has admin privileges')

# Check if default student exists
if not User.objects.filter(username='student').exists():
    # Create a new student user (without role field)
    password = 'student'
    password_hash = make_password(password)
    
    student = User.objects.create(
        username='student',
        email='student@gmail.com',
        password_hash=password_hash,
        is_email_verified=True,
        email_verified_at=timezone.now()
    )
    print('Default student user created successfully (email verified)')
else:
    student = User.objects.get(username='student')
    # Ensure existing student is verified
    if not student.is_email_verified:
        student.is_email_verified = True
        student.email_verified_at = timezone.now()
        student.save()
        print('Default student user verified')
    else:
        print('Default student user already exists and verified')

# Create default course by teacher if it doesn't exist
if not Course.objects.filter(title='Thermodynamics Course').exists():
    teacher_course = Course.objects.create(
        title='Thermodynamics Course',
        description='A comprehensive course on thermodynamics principles',
        owner=teacher
    )
    print('Teacher course created successfully')
else:
    teacher_course = Course.objects.get(title='Thermodynamics Course')
    print('Teacher course already exists')

# Create default course by student if it doesn't exist
if not Course.objects.filter(title='Student Research Project').exists():
    student_course = Course.objects.create(
        title='Student Research Project',
        description='Independent research project course',
        owner=student
    )
    print('Student course created successfully')
else:
    student_course = Course.objects.get(title='Student Research Project')
    print('Student course already exists')

# Create default activity in teacher's course if it doesn't exist
if not Activity.objects.filter(title='Thermodynamics Fundamentals').exists():
    teacher_activity = Activity.objects.create(
        title='Thermodynamics Fundamentals',
        description='Learn the basic principles of thermodynamics',
        course=teacher_course,
        owner=teacher
    )
    print('Teacher activity created successfully')
else:
    teacher_activity = Activity.objects.get(title='Thermodynamics Fundamentals')
    print('Teacher activity already exists')

# Create default activity in student's course if it doesn't exist
if not Activity.objects.filter(title='Research Discussion').exists():
    student_activity = Activity.objects.create(
        title='Research Discussion',
        description='Discuss research findings and methodologies',
        course=student_course,
        owner=student
    )
    print('Student activity created successfully')
else:
    student_activity = Activity.objects.get(title='Research Discussion')
    print('Student activity already exists')

# Enroll student in the teacher's course if not already enrolled
if not CourseEnrollment.objects.filter(user=student, course=teacher_course).exists():
    CourseEnrollment.objects.create(
        user=student,
        course=teacher_course,
        role='student'
    )
    print('Student enrolled in teacher course successfully')
else:
    print('Student already enrolled in teacher course')

# Enroll teacher as student in the student's course if not already enrolled
if not CourseEnrollment.objects.filter(user=teacher, course=student_course).exists():
    CourseEnrollment.objects.create(
        user=teacher,
        course=student_course,
        role='student'
    )
    print('Teacher enrolled as student in student course successfully')
else:
    print('Teacher already enrolled as student in student course')

from datetime import datetime
from django.utils import timezone
from simbaapp.models import Message, Thread

# Create conversation in teacher's course (student participating)
thread_teacher_course = None
threads_for_student_in_teacher_course = Thread.objects.filter(user=student, activity=teacher_activity)
if threads_for_student_in_teacher_course.exists():
    thread_teacher_course = threads_for_student_in_teacher_course.first()
    print('Thread for student in teacher course already exists')
else:
    thread_teacher_course = Thread.objects.create(
        activity=teacher_activity,
        user=student
    )
    print('Thread for student in teacher course created successfully')

    # Add thermodynamics conversation messages
    thermodynamics_messages = [
        {
            'role': 'assistant',
            'content': 'Bonjour ! Je suis SIMBA, et je vais vous aider à réfléchir aux questions suivantes:\n\n1. D\'après vous, quelles sont les 2 à 5 idées principales à retenir du cours sur la thermodynamique?\n2. Quelles questions vous posez-vous encore sur ces notions?\n3. Comment pourriez-vous mettre en pratique ces concepts?\n\nN\'hésitez pas à partager vos réflexions, et nous pourrons explorer ces sujets ensemble!'
        },
        {
            'role': 'user',
            'content': 'Je n\'ai pas compris le concept de particule. Peux-tu me l\'expliquer ?'
        },
        {
            'role': 'assistant',
            'content': 'Bien entendu ! Le concept de particule en thermodynamique est assez fondamental. Une particule, dans ce contexte, peut être vue comme une représentation d\'un composant individuel de la matière (comme un atome ou une molécule) qui possède des propriétés comme la masse, la vitesse, et l\'énergie.\n\nEn thermodynamique, lorsque nous parlons de particules, nous considérons généralement :\n\n1. **Les molécules de gaz** : Dans un gaz, les particules sont les molécules qui se déplacent librement, entrent en collision les unes avec les autres et avec les parois du récipient.\n\n2. **L\'énergie cinétique des particules** : La température d\'un système est liée à l\'énergie cinétique moyenne des particules. Plus les particules se déplacent rapidement (en moyenne), plus la température est élevée.\n\n3. **Les interactions entre particules** : Dans les liquides et les solides, les interactions entre particules sont plus fortes que dans les gaz, ce qui explique leurs propriétés différentes.\n\nAs-tu des questions spécifiques sur le rôle des particules en thermodynamique ?'
        },
        {
            'role': 'user',
            'content': 'C\'est bon merci. Et le concept de pression ?'
        },
        {
            'role': 'assistant',
            'content': 'Super, passons à la pression alors !\n\nLa pression est une notion cruciale en thermodynamique. Elle est définie comme la force exercée par unité de surface. En termes de formule : P = F/A (Pression = Force/Aire).\n\nEn thermodynamique, la pression peut être expliquée de plusieurs façons :\n\n1. **À l\'échelle microscopique** : La pression d\'un gaz résulte des collisions des particules contre les parois du récipient. Chaque fois qu\'une particule rebondit sur une paroi, elle exerce une petite force. La somme de toutes ces collisions crée la pression que nous ressentons.\n\n2. **Relation avec les autres variables** : La pression est liée à d\'autres grandeurs thermodynamiques comme la température, le volume et la quantité de matière. C\'est ce qu\'exprime la loi des gaz parfaits : PV = nRT.\n\n3. **Importance dans les processus** : Les différences de pression sont souvent les moteurs des transformations en thermodynamique (comme dans les machines thermiques, les écoulements de fluides, etc.).\n\nAvez-vous des questions sur comment la pression intervient dans les processus thermodynamiques ?'
        },
        {
            'role': 'user',
            'content': 'Quel est le lien entre pression et température ?'
        },
        {
            'role': 'assistant',
            'content': 'Ah, excellente question ! Le lien entre la pression et la température est fondamental en thermodynamique et est souvent expliqué par la loi des gaz parfaits.\n\nVoici les principales relations entre pression et température :\n\n1. **Loi de Gay-Lussac (à volume constant)** : À volume constant, la pression d\'un gaz est proportionnelle à sa température absolue. En formule : P ∝ T (à V constant). Si on double la température absolue (en kelvin), la pression double également.\n\n2. **Explication microscopique** : Lorsque la température augmente, les particules se déplacent plus rapidement et heurtent les parois du récipient avec plus de force et plus fréquemment, ce qui augmente la pression.\n\n3. **Dans la loi des gaz parfaits** : P = nRT/V. On voit directement que P est proportionnelle à T (si n, R et V restent constants).\n\n4. **Applications pratiques** : Ce lien explique pourquoi un pneu gonflé à l\'air peut sembler dégonflé par temps froid (la pression diminue avec la température) ou pourquoi il faut être prudent avec les aérosols exposés à la chaleur (la pression augmente et peut faire exploser le contenant).\n\nY a-t-il un aspect particulier de cette relation que vous aimeriez approfondir ?'
        }
    ]
    
    for idx, msg in enumerate(thermodynamics_messages):
        metadata = None
        if msg['role'] == 'user':
            metadata = {'user_id': str(student.id), 'author': 'student', 'role': 'student'}
        elif msg['role'] == 'assistant':
            # Demo text, not generated by a model; tagged so it doesn't count as GPT usage
            metadata = {'model': 'seed-data', 'user_id': str(student.id)}
            
        Message.objects.create(
            thread=thread_teacher_course,
            role=msg['role'],
            content=msg['content'],
            message_number=idx + 1,
            metadata=metadata
        )
    print('Thermodynamics conversation messages created successfully')

# Create conversation in student's course (teacher participating as student)
thread_student_course = None
threads_for_teacher_in_student_course = Thread.objects.filter(user=teacher, activity=student_activity)
if threads_for_teacher_in_student_course.exists():
    thread_student_course = threads_for_teacher_in_student_course.first()
    print('Thread for teacher in student course already exists')
else:
    thread_student_course = Thread.objects.create(
        activity=student_activity,
        user=teacher
    )
    print('Thread for teacher in student course created successfully')

    # Add research discussion messages
    research_messages = [
        {
            'role': 'assistant',
            'content': 'Hello! I\'m SIMBA, and I\'m here to help you discuss your research project. Let\'s explore:\n\n1. What is the main focus of your current research?\n2. What methodologies are you considering or using?\n3. What challenges have you encountered so far?\n\nFeel free to share your thoughts and we can discuss them together!'
        },
        {
            'role': 'user',
            'content': 'I\'m working on analyzing student engagement patterns in online learning environments. I\'m particularly interested in how different types of interactions affect learning outcomes.'
        },
        {
            'role': 'assistant',
            'content': 'That\'s a fascinating research area! Student engagement in online learning is indeed crucial for educational success. Your focus on interaction types and their impact on learning outcomes is very relevant, especially given the growth of digital education.\n\nSome questions to help guide your research:\n\n1. **Types of interactions**: Are you looking at student-content, student-instructor, or student-student interactions? Or perhaps all three?\n\n2. **Engagement metrics**: How are you measuring engagement? Are you using behavioral data (clicks, time spent), participation metrics (forum posts, assignments), or learning analytics?\n\n3. **Learning outcomes**: What specific outcomes are you measuring? Test scores, course completion rates, skill acquisition?\n\nWhat specific methodologies are you considering for data collection and analysis?'
        },
        {
            'role': 'user',
            'content': 'I\'m focusing on all three types of interactions, and I\'m using a mixed-methods approach combining learning analytics data with qualitative interviews.'
        },
        {
            'role': 'assistant',
            'content': 'Excellent approach! A mixed-methods design will give you both the breadth of quantitative data and the depth of qualitative insights. This combination is particularly powerful for understanding complex phenomena like student engagement.\n\nFor your learning analytics component, you might want to consider:\n\n**Quantitative measures:**\n- Frequency and duration of platform interactions\n- Discussion forum participation patterns\n- Assignment submission timing and quality\n- Navigation patterns through course materials\n\n**Qualitative insights from interviews:**\n- Students\' perceptions of different interaction types\n- Barriers to engagement they experience\n- Preferred communication methods and why\n\nHave you considered any specific analytical frameworks for coding your qualitative data? And what statistical methods are you planning for the quantitative analysis?'
        }
    ]
    
    for idx, msg in enumerate(research_messages):
        metadata = None
        if msg['role'] == 'user':
            metadata = {'user_id': str(teacher.id), 'author': 'prof', 'role': 'student'}
        elif msg['role'] == 'assistant':
            # Demo text, not generated by a model; tagged so it doesn't count as GPT usage
            metadata = {'model': 'seed-data', 'user_id': str(teacher.id)}
            
        Message.objects.create(
            thread=thread_student_course,
            role=msg['role'],
            content=msg['content'],
            message_number=idx + 1,
            metadata=metadata
        )
    print('Research discussion messages created successfully')

print('=== SETUP SUMMARY ===')
print(f'Teacher (prof): owns {Course.objects.filter(owner=teacher).count()} courses, enrolled in {CourseEnrollment.objects.filter(user=teacher).count()} courses')
print(f'Student (student): owns {Course.objects.filter(owner=student).count()} courses, enrolled in {CourseEnrollment.objects.filter(user=student).count()} courses')
print('Both users now have access to teacher and student views!')
"

PORT="${PORT:-8000}"

# WORKERS can be set in the environment to override the default of 2 per core.
# Careful with low values: several pages (e.g. /login/) call this same server's
# API over HTTP, so each of those requests needs two free workers. With only 2
# workers, two people logging in at once deadlocked until the 240 s timeout.
if [ -z "$WORKERS" ]; then
  CORES=$(nproc)
  WORKERS=$((CORES * 2))
  if [ "$WORKERS" -lt 2 ]; then
    WORKERS=2
  fi
fi

echo "Starting Gunicorn on port $PORT with $WORKERS workers"

exec gunicorn simba.wsgi:application \
    --bind "0.0.0.0:$PORT" \
    --workers $WORKERS \
    --worker-class sync \
    --worker-connections 1000 \
    --timeout 240 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --log-level info \
    --access-logfile - \
    --error-logfile - \
    --limit-request-line 4094 \
    --limit-request-fields 100