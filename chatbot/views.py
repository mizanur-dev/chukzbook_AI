import logging
import uuid
import re

from django.conf import settings
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_deepseek import ChatDeepSeek
from langgraph.prebuilt import create_react_agent
from rest_framework import status
from rest_framework.generics import CreateAPIView
from rest_framework.response import Response

from .serializers import (
    PublicChatRequestSerializer,
    PublicChatResponseSerializer,
    EmailSerializer,
    PremiumChatRequestSerializer,
    PremiumChatResponseSerializer,
)
from .system_prompt import SYSTEM_PROMPT, PREMIUM_SYSTEM_PROMPT
from .tools import get_search_tool, make_email_pdf_tool

logger = logging.getLogger(__name__)


HISTORY_MAX_TURNS = 10


_PUBLIC_LLM = ChatDeepSeek(
    model="deepseek-chat",
    api_key=settings.DEEPSEEK_API_KEY,
    max_tokens=512,
    temperature=0.5,
)


_PREMIUM_LLM = ChatDeepSeek(
    model="deepseek-chat",
    api_key=settings.DEEPSEEK_API_KEY,
    max_tokens=2048,
    temperature=0.5,
)


_PUBLIC_PROMPT = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    MessagesPlaceholder(variable_name="history"),
    ("human", "{input}"),
])
_PUBLIC_CHAIN = _PUBLIC_PROMPT | _PUBLIC_LLM



_premium_sessions: dict[str, dict] = {}


def _history_to_messages(history_data: list[dict]) -> list:
    """Convert serialised history dicts to LangChain message objects."""
    msgs = []
    for item in history_data:
        if item["type"] == "human":
            msgs.append(HumanMessage(content=item["content"]))
        elif item["type"] == "ai":
            msgs.append(AIMessage(content=item["content"]))
    return msgs



def normalize_response_text(text: str) -> str:
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


class PublicChatView(CreateAPIView):
    """
    POST /api/chat/
    Body: { "message": "…" }

    * No authentication, no email, no session_id.
    * Simple one-shot conversational chain — no tools, no history.
    """
    serializer_class = PublicChatRequestSerializer
    authentication_classes = []
    permission_classes = []

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_message = serializer.validated_data["message"]

        response = _PUBLIC_CHAIN.invoke({
            "input": user_message,
            "history": [],
        })

        ai_text = response.content if hasattr(response, "content") else str(response)
        ai_text = normalize_response_text(ai_text)

        return Response(
            PublicChatResponseSerializer({"response": ai_text}).data,
            status=status.HTTP_200_OK,
        )


class EmailView(CreateAPIView):
    """
    POST /api/set_email/
    Body: { "email": "user@example.com" }
    Returns: { "message": "…", "session_id": "…" }
    """
    serializer_class = EmailSerializer
    authentication_classes = []
    permission_classes = []

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        email = serializer.validated_data["email"]
        session_id = f"{email}_{uuid.uuid4().hex[:8]}"

        _premium_sessions[session_id] = {
            "email": email,
            "history": [],
        }

        return Response({
            "message": f"Email set successfully: {email}. You can now use the premium chatbot.",
            "session_id": session_id,
        }, status=status.HTTP_200_OK)


class PremiumChatView(CreateAPIView):
    """
    POST /api/chat/premium/
    Body: { "message": "…", "session_id": "…" }

    * Requires a session_id obtained from /api/set_email/.
    * Tool-calling agent with Search + Create_and_Email_PDF.
    * Uses AgentExecutor with high max_tokens to prevent truncation.
    """
    serializer_class = PremiumChatRequestSerializer
    authentication_classes = []
    permission_classes = []

    def _build_agent(self, user_email: str):
        """Create a langgraph react agent with tools bound to this user."""
        tools = [
            get_search_tool(),
            make_email_pdf_tool(user_email),
        ]

        return create_react_agent(
            model=_PREMIUM_LLM,
            tools=tools,
            prompt=PREMIUM_SYSTEM_PROMPT,
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        session_id = serializer.validated_data["session_id"]
        user_message = serializer.validated_data["message"]

        session_data = _premium_sessions.get(session_id)
        if not session_data or "email" not in session_data:
            return Response(
                {"error": "Invalid session_id. Please call /api/set_email/ first."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_email = session_data["email"]
        history_data = session_data["history"]

        max_msgs = HISTORY_MAX_TURNS * 2
        history_data = history_data[-max_msgs:]

        agent = self._build_agent(user_email)

        try:
            messages = _history_to_messages(history_data) + [
                HumanMessage(content=user_message),
            ]
            result = agent.invoke({"messages": messages})
            ai_text = result["messages"][-1].content
            ai_text = normalize_response_text(ai_text)
        except Exception:
            logger.exception("Premium agent failed for session %s", session_id)
            ai_text = (
                "I'm sorry, something went wrong while processing your request. "
                "Please try again in a moment."
            )
            ai_text = normalize_response_text(ai_text)

        history_data.append({"type": "human", "content": user_message})
        history_data.append({"type": "ai", "content": ai_text})
        session_data["history"] = history_data[-max_msgs:]

        return Response(
            PremiumChatResponseSerializer({"response": ai_text}).data,
            status=status.HTTP_200_OK,
        )