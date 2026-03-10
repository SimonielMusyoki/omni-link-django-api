from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from .models import ProductRequest, ProductRequestEvent


@shared_task
def send_request_created_to_approver(request_id: int):
    req = ProductRequest.objects.select_related('requested_by', 'approver').get(id=request_id)
    if not req.approver or not req.approver.email:
        return 'No approver email configured.'

    send_mail(
        subject=f'Product Request #{req.id} needs your approval',
        message=(
            f'A new product request has been submitted by {req.requested_by.email}.\n\n'
            f'Reason: {req.reason}\n'
            f'Please review and approve in Omni Link.'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@omnilink.local'),
        recipient_list=[req.approver.email],
        fail_silently=False,
    )
    ProductRequestEvent.objects.create(
        request=req,
        event_type=ProductRequestEvent.EMAIL_TO_APPROVER_SENT,
        note=f'Approval request email sent to {req.approver.email}.',
        metadata={'recipient': req.approver.email},
    )
    return 'Approver notification sent.'


@shared_task
def send_request_approved_to_manager(request_id: int):
    req = ProductRequest.objects.select_related('warehouse__manager').get(id=request_id)
    manager = req.warehouse.manager if req.warehouse else None
    if not manager or not manager.email:
        return 'No warehouse manager email configured.'

    send_mail(
        subject=f'Product Request #{req.id} approved - please pack items',
        message=(
            f'Product Request #{req.id} has been approved and assigned to {req.warehouse.name}.\n\n'
            f'Please prepare items for collection.'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@omnilink.local'),
        recipient_list=[manager.email],
        fail_silently=False,
    )
    ProductRequestEvent.objects.create(
        request=req,
        event_type=ProductRequestEvent.EMAIL_TO_MANAGER_SENT,
        note=f'Packing notification email sent to {manager.email}.',
        metadata={'recipient': manager.email},
    )
    return 'Manager notification sent.'


@shared_task
def send_request_ready_to_collect_to_requester(request_id: int):
    req = ProductRequest.objects.select_related('requested_by', 'warehouse').get(id=request_id)
    if not req.requested_by.email:
        return 'No requester email configured.'

    send_mail(
        subject=f'Product Request #{req.id} is ready for collection',
        message=(
            f'Your product request #{req.id} is now ready to collect from '
            f'{req.warehouse.name if req.warehouse else "the warehouse"}.'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@omnilink.local'),
        recipient_list=[req.requested_by.email],
        fail_silently=False,
    )
    ProductRequestEvent.objects.create(
        request=req,
        event_type=ProductRequestEvent.EMAIL_TO_REQUESTER_SENT,
        note=f'Ready-for-collection email sent to {req.requested_by.email}.',
        metadata={'recipient': req.requested_by.email},
    )
    return 'Requester notification sent.'
