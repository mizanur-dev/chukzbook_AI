from django.urls import path

from .views import BookIdeaCheckAPIView

urlpatterns = [
    path("check/", BookIdeaCheckAPIView.as_view(), name="book-idea-check"),
]
