from rest_framework import status
from rest_framework.generics import CreateAPIView
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, NotFound
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_deepseek import ChatDeepSeek
from langchain_core.messages import AIMessage, HumanMessage
from .serializers import ChatRequestSerializer, ChatResponseSerializer, EmailSerializer
from django.conf import settings
from django.contrib.sessions.models import Session
import uuid
from .system_prompt import SYSTEM_PROMPT



HISTORY_MAX_TURNS = 10 

_LLM = ChatDeepSeek(
    model="deepseek-chat",
    api_key=settings.DEEPSEEK_API_KEY,
    max_tokens=512,
    temperature=0.5
)

_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}"),
])

_CHAIN = _PROMPT | _LLM


def _normalize_response(text: str) -> str:
    lines = text.splitlines()
    cleaned_lines = []
    for line in lines:
        l = line.strip()
     
        for prefix in ("- ", "* ", "• "):
            if l.startswith(prefix):
                l = l[len(prefix):].strip()
                break
   
        while l.startswith('#'):
            l = l.lstrip('#').strip()
        cleaned_lines.append(l)
    cleaned = ' '.join(cleaned_lines)
    cleaned = cleaned.replace('```', '').replace('`', '').replace('*', '')
    cleaned = cleaned.replace('_', '')
    cleaned = ' '.join(cleaned.split())
    return cleaned

class EmailView(CreateAPIView):
    serializer_class = EmailSerializer
    authentication_classes = []
    permission_classes = []

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        email = serializer.validated_data['email']
        
        session_id = f"{email}_{uuid.uuid4().hex[:8]}"

        request.session.create()
        
        request.session['user_email'] = email
        request.session['custom_session_id'] = session_id
        
        return Response({
            "message": f"Email set successfully: {email}. You can now use the chatbot.",
            "session_id": session_id
        }, status=status.HTTP_200_OK)

class ChatView(CreateAPIView):
    serializer_class = ChatRequestSerializer
    authentication_classes = []
    permission_classes = []

    def get_session_by_custom_id(self, session_id):
        """Find session by our custom session ID"""
        sessions = Session.objects.all()
        for session in sessions:
            session_data = session.get_decoded()
            if session_data.get('custom_session_id') == session_id:
                return session, session_data
        return None, None

    def create(self, request, *args, **kwargs):
        serializer = ChatRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        user_message = serializer.validated_data['message']
        session_id = serializer.validated_data['session_id']
        
        if not session_id:
            raise ValidationError("Session ID is required")
        
        session_obj, session_data = self.get_session_by_custom_id(session_id)
        
        if not session_obj or not session_data:
            raise NotFound("Invalid session ID. Please set your email first via /api/set_email/")
        
        if 'user_email' not in session_data:
            raise NotFound("Invalid session. Please set your email first via /api/set_email/")
        
        chat_history_key = f'chat_history_{session_id}'
        history_data = session_data.get(chat_history_key, [])

        max_messages = HISTORY_MAX_TURNS * 2
        trimmed_history = history_data[-max_messages:]

        history_msgs = []
        for item in trimmed_history:
            if item.get('type') == 'human':
                history_msgs.append(HumanMessage(content=item.get('content', '')))
            elif item.get('type') == 'ai':
                history_msgs.append(AIMessage(content=item.get('content', '')))

        response = _CHAIN.invoke({
            "input": user_message,
            "history": history_msgs,
        })

        ai_text = response.content if hasattr(response, "content") else str(response)
        ai_text = _normalize_response(ai_text)

        updated_history = trimmed_history + [
            {"type": "human", "content": user_message},
            {"type": "ai", "content": ai_text},
        ]
        updated_history = updated_history[-max_messages:]

        session_data[chat_history_key] = updated_history
        session_obj.session_data = Session.objects.encode(session_data)
        session_obj.save()

        response_serializer = ChatResponseSerializer({'response': ai_text})
        return Response(response_serializer.data, status=status.HTTP_200_OK)