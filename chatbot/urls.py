from django.urls import path
from .views import PublicChatView, EmailView, PremiumChatView

urlpatterns = [
    path('chat/', PublicChatView.as_view(), name='public_chat'),
    path('set_email/', EmailView.as_view(), name='set_email'),
    path('chat/premium/', PremiumChatView.as_view(), name='premium_chat'),
]
