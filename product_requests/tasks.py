from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .models import ProductRequest, ProductRequestEvent

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_APP_URL = 'https://wms.uncoverskincare.co'


def _request_url(request_id: int) -> str:
    return f'{_APP_URL}/requests/{request_id}'


# ─────────────────────────────────────────────────────────────────────────────
# HTML / text helpers
# ─────────────────────────────────────────────────────────────────────────────

def _product_rows_html(req) -> str:
    """Build <tr> rows for the product table. Uses the prefetch cache on req.items.

    Layout mirrors Shopify order emails:
      [ thumbnail ] ← 10 px gap → [ Product name (bold) / SKU (grey) ]   |   Qty
    """
    rows = []
    for item in req.items.all():
        sku = item.product.sku or '—'
        image_url = (item.product.image_url or '').strip()

        if image_url:
            img_cell = (
                f'<td style="width:52px;padding-right:10px;vertical-align:middle;">'
                f'<img src="{image_url}" width="52" height="52" alt="" '
                f'style="display:block;width:52px;height:52px;border-radius:6px;'
                f'border:1px solid #ebebeb;object-fit:cover;" /></td>'
            )
        else:
            # Neutral placeholder box when no image is stored
            img_cell = (
                f'<td style="width:52px;padding-right:10px;vertical-align:middle;">'
                f'<table role="presentation" cellpadding="0" cellspacing="0">'
                f'<tr><td style="width:52px;height:52px;background:#f4f5f7;border-radius:6px;'
                f'border:1px solid #ebebeb;text-align:center;vertical-align:middle;'
                f'font-size:22px;color:#ccc;">&#128230;</td></tr>'
                f'</table></td>'
            )

        rows.append(
            f'<tr>'
            # ── Product cell ──────────────────────────────────────────────
            f'<td style="padding:12px 14px;border-bottom:1px solid #f0f0f0;vertical-align:middle;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
            f'{img_cell}'
            f'<td style="vertical-align:middle;">'
            f'<p style="margin:0;font-size:14px;font-weight:600;color:#1a1a1a;line-height:1.3;">'
            f'{item.product.name}</p>'
            f'<p style="margin:3px 0 0;font-size:12px;color:#999;line-height:1.3;">SKU:&nbsp;{sku}</p>'
            f'</td>'
            f'</tr></table>'
            f'</td>'
            # ── Qty cell ──────────────────────────────────────────────────
            f'<td style="padding:12px 14px;border-bottom:1px solid #f0f0f0;text-align:center;'
            f'vertical-align:middle;font-size:14px;font-weight:700;color:#1a1a1a;white-space:nowrap;">'
            f'{item.quantity}'
            f'</td>'
            f'</tr>'
        )

    if not rows:
        return (
            '<tr><td colspan="2" style="padding:16px;text-align:center;'
            'font-size:14px;color:#aaa;">No items listed.</td></tr>'
        )
    return ''.join(rows)


def _product_rows_text(req) -> str:
    lines = []
    for item in req.items.all():
        sku = f' ({item.product.sku})' if item.product.sku else ''
        lines.append(f'  • {item.product.name}{sku}  —  Qty: {item.quantity}')
    return '\n'.join(lines) if lines else '  (no items)'


def _build_html(
    *,
    accent: str,
    icon: str,
    title: str,
    greeting: str,
    paragraphs: list,
    items_html: str,
    button_label: str,
    button_url: str,
    extra_note: str = '',
) -> str:
    para_html = ''.join(
        f'<p style="margin:0 0 14px;font-size:15px;color:#444;line-height:1.7;">{p}</p>'
        for p in paragraphs
    )
    note_html = (
        f'<p style="margin:14px 0 0;font-size:12px;color:#c0c0c0;font-style:italic;">{extra_note}</p>'
        if extra_note else ''
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>{title}</title>
</head>
<body style="margin:0;padding:0;background:#f0f2f5;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#f0f2f5;padding:48px 16px;">
    <tr>
      <td align="center">
        <table role="presentation" cellpadding="0" cellspacing="0"
               style="width:100%;max-width:580px;">

          <!-- ── Header ─────────────────────────────────────────── -->
          <tr>
            <td style="background:{accent};border-radius:12px 12px 0 0;
                       padding:30px 36px;text-align:center;">
              <p style="margin:0;font-size:11px;font-weight:700;letter-spacing:2px;
                         text-transform:uppercase;color:rgba(255,255,255,0.7);">
                Omni&nbsp;Link &middot; Uncover&nbsp;Skincare
              </p>
              <p style="margin:10px 0 0;font-size:22px;font-weight:700;color:#fff;">
                {icon}&nbsp; {title}
              </p>
            </td>
          </tr>

          <!-- ── Body ───────────────────────────────────────────── -->
          <tr>
            <td style="background:#ffffff;padding:36px 36px 30px;">

              <p style="margin:0 0 20px;font-size:17px;font-weight:600;color:#111;">
                {greeting}
              </p>

              {para_html}

              <!-- Products table -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                     style="margin:22px 0 28px;border:1px solid #ebebeb;border-radius:8px;
                            overflow:hidden;border-collapse:collapse;">
                <thead>
                  <tr style="background:#f7f8fa;">
                    <th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:700;
                               letter-spacing:1px;text-transform:uppercase;color:#999;
                               border-bottom:1px solid #ebebeb;">Product</th>
                    <th style="padding:10px 14px;text-align:center;font-size:11px;font-weight:700;
                               letter-spacing:1px;text-transform:uppercase;color:#999;
                               border-bottom:1px solid #ebebeb;">Qty</th>
                  </tr>
                </thead>
                <tbody>
                  {items_html}
                </tbody>
              </table>

              <!-- CTA button -->
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                <tr>
                  <td align="center">
                    <a href="{button_url}"
                       style="display:inline-block;background:{accent};color:#ffffff;
                              text-decoration:none;font-size:15px;font-weight:600;
                              padding:14px 42px;border-radius:7px;letter-spacing:0.3px;">
                      {button_label} &rarr;
                    </a>
                  </td>
                </tr>
              </table>

            </td>
          </tr>

          <!-- ── Footer ─────────────────────────────────────────── -->
          <tr>
            <td style="background:#f7f8fa;border-top:1px solid #ebebeb;
                       border-radius:0 0 12px 12px;padding:20px 36px;text-align:center;">
              <p style="margin:0;font-size:12px;color:#c0c0c0;">
                Automated notification &bull;
                <a href="{_APP_URL}" style="color:#c0c0c0;text-decoration:underline;">
                  Omni Link WMS
                </a>
              </p>
              {note_html}
            </td>
          </tr>

        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""


def _build_text(
    *,
    title: str,
    greeting: str,
    paragraphs: list,
    items_text: str,
    button_label: str,
    button_url: str,
    extra_note: str = '',
) -> str:
    body = '\n\n'.join(paragraphs)
    note = f'\n\nNote: {extra_note}' if extra_note else ''
    sep = '─' * 46
    return (
        f"Omni Link · Uncover Skincare\n{sep}\n"
        f"{title}\n{sep}\n\n"
        f"{greeting}\n\n"
        f"{body}\n\n"
        f"REQUESTED ITEMS\n{sep}\n"
        f"{items_text}\n"
        f"{sep}\n\n"
        f"{button_label}:\n{button_url}\n\n"
        f"{sep}\n"
        f"Automated notification — Omni Link WMS\n{_APP_URL}"
        f"{note}\n"
    )


def _send(*, subject: str, from_email: str, to: str, html: str, text: str) -> None:
    msg = EmailMultiAlternatives(
        subject=subject,
        body=text,
        from_email=from_email,
        to=[to],
    )
    msg.attach_alternative(html, 'text/html')
    msg.send()


# ─────────────────────────────────────────────────────────────────────────────
# Tasks
# ─────────────────────────────────────────────────────────────────────────────

@shared_task
def send_request_created_to_approver(request_id: int):
    req = (
        ProductRequest.objects
        .select_related('requested_by', 'approver')
        .prefetch_related('items__product')
        .get(id=request_id)
    )
    if not req.approver or not req.approver.email:
        return 'No approver email configured.'

    url = _request_url(request_id)
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@omnilink.local')
    approver_name = req.approver.get_full_name() or req.approver.email.split('@')[0].title()
    requester_name = req.requested_by.get_full_name() or req.requested_by.email

    html = _build_html(
        accent='#f59e0b',
        icon='📋',
        title=f'Request #{req.id} Awaiting Your Approval',
        greeting=f'Hello {approver_name},',
        paragraphs=[
            f'<strong>{requester_name}</strong> has submitted a new product request that requires your approval.',
            f'<strong>Reason:</strong> {req.reason}',
            'Please review the items below and approve or reject the request directly from the Omni Link portal.',
        ],
        items_html=_product_rows_html(req),
        button_label='Review &amp; Approve Request',
        button_url=url,
        extra_note='If you did not expect this email, please contact your system administrator.',
    )
    text = _build_text(
        title=f'Request #{req.id} Awaiting Your Approval',
        greeting=f'Hello {approver_name},',
        paragraphs=[
            f'{requester_name} has submitted a new product request that requires your approval.',
            f'Reason: {req.reason}',
            'Please review the items and approve or reject the request from the Omni Link portal.',
        ],
        items_text=_product_rows_text(req),
        button_label='Review & Approve Request',
        button_url=url,
        extra_note='If you did not expect this email, please contact your system administrator.',
    )
    _send(
        subject=f'[Action Required] Product Request #{req.id} Needs Your Approval',
        from_email=from_email,
        to=req.approver.email,
        html=html,
        text=text,
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
    req = (
        ProductRequest.objects
        .select_related('warehouse__manager')
        .prefetch_related('items__product')
        .get(id=request_id)
    )
    manager = req.warehouse.manager if req.warehouse else None
    if not manager or not manager.email:
        return 'No warehouse manager email configured.'

    url = _request_url(request_id)
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@omnilink.local')
    manager_name = manager.get_full_name() or manager.email.split('@')[0].title()
    warehouse_name = req.warehouse.name if req.warehouse else 'the warehouse'

    html = _build_html(
        accent='#3b82f6',
        icon='📦',
        title=f'Request #{req.id} — Please Prepare Items',
        greeting=f'Hello {manager_name},',
        paragraphs=[
            f'Product Request <strong>#{req.id}</strong> has been approved and assigned to '
            f'<strong>{warehouse_name}</strong>.',
            'Please pick and pack the items listed below, then mark the request as '
            '<em>Ready to Collect</em> so the requester receives a notification.',
        ],
        items_html=_product_rows_html(req),
        button_label='Mark as Ready to Collect',
        button_url=url,
    )
    text = _build_text(
        title=f'Request #{req.id} — Please Prepare Items',
        greeting=f'Hello {manager_name},',
        paragraphs=[
            f'Product Request #{req.id} has been approved and assigned to {warehouse_name}.',
            'Please pick and pack the items listed below, then mark the request as '
            'Ready to Collect so the requester receives a notification.',
        ],
        items_text=_product_rows_text(req),
        button_label='Mark as Ready to Collect',
        button_url=url,
    )
    _send(
        subject=f'[Action Required] Product Request #{req.id} — Please Pack Items',
        from_email=from_email,
        to=manager.email,
        html=html,
        text=text,
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
    req = (
        ProductRequest.objects
        .select_related('requested_by', 'warehouse')
        .prefetch_related('items__product')
        .get(id=request_id)
    )
    if not req.requested_by.email:
        return 'No requester email configured.'

    url = _request_url(request_id)
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@omnilink.local')
    requester_name = req.requested_by.get_full_name() or req.requested_by.email.split('@')[0].title()
    warehouse_name = req.warehouse.name if req.warehouse else 'the warehouse'

    html = _build_html(
        accent='#10b981',
        icon='✅',
        title=f'Your Request #{req.id} is Ready to Collect!',
        greeting=f'Hello {requester_name},',
        paragraphs=[
            f'Great news! Your product request <strong>#{req.id}</strong> has been packed and is '
            f'ready for collection from <strong>{warehouse_name}</strong>.',
            'The items below are waiting for you. Please collect them at your earliest convenience.',
        ],
        items_html=_product_rows_html(req),
        button_label='View Request',
        button_url=url,
        extra_note='Please bring this email or your Request ID when collecting.',
    )
    text = _build_text(
        title=f'Your Request #{req.id} is Ready to Collect!',
        greeting=f'Hello {requester_name},',
        paragraphs=[
            f'Great news! Your product request #{req.id} has been packed and is '
            f'ready for collection from {warehouse_name}.',
            'The items below are waiting for you. Please collect them at your earliest convenience.',
        ],
        items_text=_product_rows_text(req),
        button_label='View Request',
        button_url=url,
        extra_note='Please bring this email or your Request ID when collecting.',
    )
    _send(
        subject=f'Your Product Request #{req.id} is Ready for Collection 🎉',
        from_email=from_email,
        to=req.requested_by.email,
        html=html,
        text=text,
    )
    ProductRequestEvent.objects.create(
        request=req,
        event_type=ProductRequestEvent.EMAIL_TO_REQUESTER_SENT,
        note=f'Ready-for-collection email sent to {req.requested_by.email}.',
        metadata={'recipient': req.requested_by.email},
    )
    return 'Requester notification sent.'
