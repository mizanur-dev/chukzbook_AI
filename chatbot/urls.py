from django.urls import path
from .views import ChatView, EmailView

urlpatterns = [
    path('set_email/', EmailView.as_view(), name='set_email'),
    path('chat/', ChatView.as_view(), name='chat'),
]
