from rest_framework import serializers
from .models import Market


class MarketSerializer(serializers.ModelSerializer):
    """Serializer for Market model"""

    class Meta:
        model = Market
        fields = [
            'id',
            'name',
            'code',
            'currency',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def validate_code(self, value):
        """Ensure code is uppercase"""
        return value.upper()

