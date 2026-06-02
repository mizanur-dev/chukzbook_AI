from rest_framework import serializers


class PublicChatRequestSerializer(serializers.Serializer):
    """Public bot: just a message, nothing else."""
    message = serializers.CharField(max_length=2000)


class PublicChatResponseSerializer(serializers.Serializer):
    response = serializers.CharField()


class EmailSerializer(serializers.Serializer):
    """Step 1 of premium flow: provide email, get session_id."""
    email = serializers.EmailField(required=True)


class PremiumChatRequestSerializer(serializers.Serializer):
    """Step 2 of premium flow: message + session_id."""
    message = serializers.CharField(max_length=2000)
    session_id = serializers.CharField(required=True)


class PremiumChatResponseSerializer(serializers.Serializer):
    response = serializers.CharField()