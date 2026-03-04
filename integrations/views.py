from rest_framework import viewsets, filters
from rest_framework.permissions import IsAuthenticated
from django_filters.rest_framework import DjangoFilterBackend

from products.models import Integration
from .serializers import IntegrationSerializer


class IntegrationViewSet(viewsets.ModelViewSet):
    """ViewSet for Integration model"""

    serializer_class = IntegrationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['warehouse', 'type', 'status']
    search_fields = ['name', 'type']
    ordering_fields = ['created_at', 'status']
    ordering = ['-created_at']

    def get_queryset(self):
        """Return integrations for current user"""
        user = self.request.user
        return Integration.objects.filter(owner=user)

    def perform_create(self, serializer):
        """Create integration with current user as owner"""
        serializer.save(owner=self.request.user)

