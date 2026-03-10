from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import ProductRequest, ProductRequestEvent
from .tasks import (
    send_request_created_to_approver,
    send_request_approved_to_manager,
    send_request_ready_to_collect_to_requester,
)


def _log_event(*, req: ProductRequest, event_type: str, actor=None, note: str = '', metadata=None):
    ProductRequestEvent.objects.create(
        request=req,
        event_type=event_type,
        actor=actor,
        note=note,
        metadata=metadata or {},
    )


@transaction.atomic
def create_request(*, user, serializer):
    req = serializer.save(requested_by=user)
    _log_event(
        req=req,
        event_type=ProductRequestEvent.REQUEST_CREATED,
        actor=user,
        note=f'{user.email} submitted this request.',
    )
    transaction.on_commit(lambda: send_request_created_to_approver.delay(req.id))
    return req


@transaction.atomic
def approve_request(*, req: ProductRequest, actor):
    if req.status != ProductRequest.PENDING:
        raise ValidationError(f'Cannot approve {req.status.lower()} request')
    if req.approver_id and req.approver_id != actor.id and not actor.is_superuser:
        raise PermissionDenied('Only the assigned approver can approve this request.')

    req.status = ProductRequest.APPROVED
    req.approved_at = timezone.now()
    req.approved_by = actor
    req.save(update_fields=['status', 'approved_at', 'approved_by', 'updated_at'])
    _log_event(
        req=req,
        event_type=ProductRequestEvent.REQUEST_APPROVED,
        actor=actor,
        note=f'{actor.email} approved this request.',
    )

    transaction.on_commit(lambda: send_request_approved_to_manager.delay(req.id))
    return req


@transaction.atomic
def reject_request(*, req: ProductRequest, actor, reason: str):
    if req.status != ProductRequest.PENDING:
        raise ValidationError(f'Cannot reject {req.status.lower()} request')
    if req.approver_id and req.approver_id != actor.id and not actor.is_superuser:
        raise PermissionDenied('Only the assigned approver can reject this request.')

    req.status = ProductRequest.REJECTED
    req.rejection_reason = reason
    req.approved_by = actor
    req.save(update_fields=['status', 'rejection_reason', 'approved_by', 'updated_at'])
    _log_event(
        req=req,
        event_type=ProductRequestEvent.REQUEST_REJECTED,
        actor=actor,
        note=f'{actor.email} rejected this request.',
        metadata={'reason': reason},
    )
    return req


@transaction.atomic
def mark_ready_to_collect(*, req: ProductRequest, actor):
    if req.status != ProductRequest.APPROVED:
        raise ValidationError('Only approved requests can be marked ready to collect.')

    manager = req.warehouse.manager if req.warehouse else None
    if manager and manager.id != actor.id and not actor.is_superuser:
        raise PermissionDenied('Only the assigned warehouse manager can mark this request ready.')

    req.status = ProductRequest.READY_TO_COLLECT
    req.ready_at = timezone.now()
    req.save(update_fields=['status', 'ready_at', 'updated_at'])
    _log_event(
        req=req,
        event_type=ProductRequestEvent.REQUEST_READY_TO_COLLECT,
        actor=actor,
        note=f'{actor.email} marked this request ready to collect.',
    )

    transaction.on_commit(lambda: send_request_ready_to_collect_to_requester.delay(req.id))
    return req
