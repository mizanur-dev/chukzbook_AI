from rest_framework import serializers

MAX_WORDS = 500
MIN_CHARS = 50


class BookIdeaCheckSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    author_brief_text = serializers.CharField()

    def validate_author_brief_text(self, value):
        if len(value) < MIN_CHARS:
            raise serializers.ValidationError(
                "Tell us a bit more about your book — at least a sentence or two."
            )
        words = value.split()
        if len(words) > MAX_WORDS:
            value = " ".join(words[:MAX_WORDS])
        return value
