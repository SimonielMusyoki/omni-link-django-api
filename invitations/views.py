from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
import secrets

from .models import Invitation
from .serializers import InvitationSerializer


class InvitationViewSet(viewsets.ModelViewSet):
    """ViewSet for Invitation model"""

    serializer_class = InvitationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['status', 'warehouse']
    search_fields = ['email']
    ordering_fields = ['created_at', 'status']
    ordering = ['-created_at']

    def get_queryset(self):
        """Return invitations sent or received by current user"""
        user = self.request.user
        return Invitation.objects.filter(
            models.Q(invited_by=user) | models.Q(invited_user=user)
        )

    def perform_create(self, serializer):
        """Create invitation with current user as inviter"""
        serializer.save(
            invited_by=self.request.user,
            token=secrets.token_urlsafe(32),
            expires_at=timezone.now() + timezone.timedelta(days=7)
        )

    @action(detail=True, methods=['post'])
    def accept(self, request, pk=None):
        """Accept invitation"""
        invitation = self.get_object()

        if invitation.status != Invitation.PENDING:
            return Response(
                {'error': f'Cannot accept {invitation.status.lower()} invitation'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if timezone.now() > invitation.expires_at:
            return Response(
                {'error': 'Invitation has expired'},
                status=status.HTTP_400_BAD_REQUEST
            )

        invitation.status = Invitation.ACCEPTED
        invitation.invited_user = request.user
        invitation.accepted_at = timezone.now()
        invitation.save()

        return Response(
            InvitationSerializer(invitation).data,
            status=status.HTTP_200_OK
        )

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject invitation"""
        invitation = self.get_object()

        if invitation.status != Invitation.PENDING:
            return Response(
                {'error': f'Cannot reject {invitation.status.lower()} invitation'},
                status=status.HTTP_400_BAD_REQUEST
            )

        invitation.status = Invitation.REJECTED
        invitation.save()

        return Response(
            InvitationSerializer(invitation).data,
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=['post'])
    def accept_by_token(self, request):
        """Accept invitation by token"""
        token = request.data.get('token')

        if not token:
            return Response(
                {'error': 'Token is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            invitation = Invitation.objects.get(token=token)

            if invitation.status != Invitation.PENDING:
                return Response(
                    {'error': f'Cannot accept {invitation.status.lower()} invitation'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if timezone.now() > invitation.expires_at:
                return Response(
                    {'error': 'Invitation has expired'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            invitation.status = Invitation.ACCEPTED
            invitation.invited_user = request.user
            invitation.accepted_at = timezone.now()
            invitation.save()

            return Response(
                InvitationSerializer(invitation).data,
                status=status.HTTP_200_OK
            )

        except Invitation.DoesNotExist:
            return Response(
                {'error': 'Invalid token'},
                status=status.HTTP_404_NOT_FOUND
            )

