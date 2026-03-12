from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import ProductRequest
from .serializers import ProductRequestSerializer
from . import services


class RequestViewSet(viewsets.ModelViewSet):
    """ViewSet for Request model"""

    serializer_class = ProductRequestSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['reason']
    ordering_fields = ['created_at', 'status']
    ordering = ['-created_at']

    def get_queryset(self):
        """Return all requests for elevated roles; otherwise only requester's own."""
        user = self.request.user
        queryset = ProductRequest.objects.all()
        if not user.is_manager():
            queryset = queryset.filter(requested_by=user)

        return (
            queryset
            .select_related('requested_by', 'approver', 'approved_by', 'warehouse')
            .prefetch_related('items__product', 'events__actor')
            .distinct()
        )

    def perform_create(self, serializer):
        """Create request and notify approver"""
        services.create_request(user=self.request.user, serializer=serializer)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve request"""
        req = self.get_object()
        try:
            req = services.approve_request(req=req, actor=request.user)
        except PermissionDenied as exc:
            return Response({'error': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return Response({'error': str(exc.detail)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ProductRequestSerializer(req).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject request"""
        req = self.get_object()
        rejection_reason = request.data.get('reason', '')
        try:
            req = services.reject_request(req=req, actor=request.user, reason=rejection_reason)
        except PermissionDenied as exc:
            return Response({'error': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return Response({'error': str(exc.detail)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ProductRequestSerializer(req).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='ready-to-collect')
    def ready_to_collect(self, request, pk=None):
        """Mark approved request as ready for collection"""
        req = self.get_object()
        try:
            req = services.mark_ready_to_collect(req=req, actor=request.user)
        except PermissionDenied as exc:
            return Response({'error': str(exc)}, status=status.HTTP_403_FORBIDDEN)
        except ValidationError as exc:
            return Response({'error': str(exc.detail)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ProductRequestSerializer(req).data, status=status.HTTP_200_OK)
