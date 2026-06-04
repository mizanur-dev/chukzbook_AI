from django.db import models


class IdeaSubmission(models.Model):
    name = models.CharField(max_length=255)
    email = models.EmailField()
    author_brief_text = models.TextField()
    ip_address = models.GenericIPAddressField()
    generated_json = models.JSONField(null=True, blank=True)
    llm_provider_used = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} - {self.email} ({self.created_at})"
