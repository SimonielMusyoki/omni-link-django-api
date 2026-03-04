from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone

from .models import ProductRequest
from .serializers import ProductRequestSerializer


class RequestViewSet(viewsets.ModelViewSet):
    """ViewSet for Request model"""

    serializer_class = ProductRequestSerializer
    permission_classes = [IsAuthenticated]
    filters = ['status', 'type', 'assigned_to']
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'status', 'type']
    ordering = ['-created_at']

    def get_queryset(self):
        """Return requests for current user (created or assigned)"""
        user = self.request.user
        return ProductRequest.objects.filter(
            models.Q(requested_by=user) | models.Q(assigned_to=user)
        )

    def perform_create(self, serializer):
        """Create request with current user as requester"""
        serializer.save(requested_by=self.request.user)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve request"""
        req = self.get_object()

        if req.status != ProductRequest.PENDING:
            return Response(
                {'error': f'Cannot approve {req.status.lower()} request'},
                status=status.HTTP_400_BAD_REQUEST
            )

        req.status = Request.APPROVED
        req.approved_at = timezone.now()
        req.approved_by = request.user
        req.save()

        return Response(
            RequestSerializer(req).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject request"""
        req = self.get_object()

        if req.status != Request.PENDING:
            return Response(
                {'error': f'Cannot reject {req.status.lower()} request'},
                status=status.HTTP_400_BAD_REQUEST
            )

        rejection_reason = request.data.get('reason', '')

        req.status = Request.REJECTED
        req.rejection_reason = rejection_reason
        req.approved_by = request.user
        req.save()

        return Response(
            RequestSerializer(req).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """Assign request to user"""
        req = self.get_object()
        assigned_to_id = request.data.get('assigned_to')

        if not assigned_to_id:
            return Response(
                {'error': 'assigned_to is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        from django.contrib.auth import get_user_model
        User = get_user_model()

        try:
            assigned_user = User.objects.get(id=assigned_to_id)
            req.assigned_to = assigned_user
            req.save()

            return Response(
                RequestSerializer(req).data,
                status=status.HTTP_200_OK
            )
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_404_NOT_FOUND
            )


# Fix import
from django.db import models

