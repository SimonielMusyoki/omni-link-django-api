"""
Service layer for inventory operations.

All write operations that touch Inventory rows go through here so that
we can guarantee:
  • atomic transactions
  • row-level locking (select_for_update) to prevent race conditions
  • consistent business-rule validation
"""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .models import Inventory, InventoryTransfer, ProductBundle


def add_stock(*, product, warehouse, quantity: int, user=None) -> Inventory:
    """
    Add *quantity* units of *product* to *warehouse*.

    Creates the Inventory row if it doesn't exist yet (get_or_create),
    then increments quantity inside a transaction with a row lock.
    """
    if quantity <= 0:
        raise ValidationError({'quantity': 'Must be a positive integer.'})
    if not product.is_physical:
        raise ValidationError({'detail': 'Virtual products do not track inventory.'})

    with transaction.atomic():
        inv, _created = Inventory.objects.select_for_update().get_or_create(
            product=product,
            warehouse=warehouse,
            defaults={'quantity': 0},
        )
        inv.quantity += quantity
        inv.save(update_fields=['quantity', 'updated_at'])
    return inv


def remove_stock(*, product, warehouse, quantity: int, user=None) -> Inventory:
    """
    Remove *quantity* units of *product* from *warehouse*.

    Validates that enough stock (unreserved) is available before
    decrementing.  Uses select_for_update to avoid races.
    """
    if quantity <= 0:
        raise ValidationError({'quantity': 'Must be a positive integer.'})
    if not product.is_physical:
        raise ValidationError({'detail': 'Virtual products do not track inventory.'})

    with transaction.atomic():
        try:
            inv = (
                Inventory.objects
                .select_for_update()
                .get(product=product, warehouse=warehouse)
            )
        except Inventory.DoesNotExist:
            raise ValidationError(
                {'detail': 'No inventory record for this product/warehouse.'}
            )

        if inv.available < quantity:
            raise ValidationError({
                'detail': (
                    f'Insufficient stock. Available: {inv.available}, '
                    f'requested: {quantity}.'
                )
            })

        inv.quantity -= quantity
        inv.save(update_fields=['quantity', 'updated_at'])
    return inv


def transfer_stock(
    *,
    product,
    from_warehouse,
    to_warehouse,
    quantity: int,
    notes: str = '',
    user=None,
) -> InventoryTransfer:
    """
    Atomically move *quantity* units from *from_warehouse* to *to_warehouse*.

    1. Lock the source Inventory row.
    2. Validate sufficient available stock.
    3. Decrement source, increment destination.
    4. Record a completed InventoryTransfer.

    If anything fails the entire transaction rolls back.
    """
    if quantity <= 0:
        raise ValidationError({'quantity': 'Must be a positive integer.'})
    if not product.is_physical:
        raise ValidationError({'detail': 'Virtual products do not track inventory.'})

    if from_warehouse.pk == to_warehouse.pk:
        raise ValidationError(
            {'detail': 'Source and destination warehouse must be different.'}
        )

    with transaction.atomic():
        # Lock source row
        try:
            source = (
                Inventory.objects
                .select_for_update()
                .get(product=product, warehouse=from_warehouse)
            )
        except Inventory.DoesNotExist:
            raise ValidationError(
                {'detail': 'No inventory for this product in the source warehouse.'}
            )

        if source.available < quantity:
            raise ValidationError({
                'detail': (
                    f'Insufficient stock in {from_warehouse.name}. '
                    f'Available: {source.available}, requested: {quantity}.'
                )
            })

        # Decrement source
        source.quantity -= quantity
        source.save(update_fields=['quantity', 'updated_at'])

        # Increment destination (create row if needed)
        dest, _ = Inventory.objects.select_for_update().get_or_create(
            product=product,
            warehouse=to_warehouse,
            defaults={'quantity': 0},
        )
        dest.quantity += quantity
        dest.save(update_fields=['quantity', 'updated_at'])

        # Record transfer
        transfer = InventoryTransfer.objects.create(
            product=product,
            from_warehouse=from_warehouse,
            to_warehouse=to_warehouse,
            quantity=quantity,
            status=InventoryTransfer.Status.COMPLETED,
            notes=notes,
            initiated_by=user,
            completed_at=timezone.now(),
        )

    return transfer


# ---------------------------------------------------------------------------
# Bundle assembly / disassembly
# ---------------------------------------------------------------------------


def assemble_bundle(*, bundle, warehouse, quantity: int, user=None) -> Inventory:
    """
    Assemble *quantity* units of a bundle in *warehouse*.

    For each component defined in ProductBundle:
        component_needed = bundle_item.quantity × quantity
    Those components are consumed (removed) from the warehouse and the
    bundle's own Inventory row is incremented.

    Everything happens inside a single atomic transaction with row locks.
    """
    if quantity <= 0:
        raise ValidationError({'quantity': 'Must be a positive integer.'})

    if not bundle.is_bundle:
        raise ValidationError({'detail': 'Product is not a bundle.'})
    if not bundle.is_physical:
        raise ValidationError({'detail': 'Virtual products do not track inventory.'})

    components = list(ProductBundle.objects.filter(bundle=bundle).select_related('component'))
    if not components:
        raise ValidationError({'detail': 'Bundle has no components defined.'})

    with transaction.atomic():
        # Lock & validate every component row first
        for item in components:
            needed = item.quantity * quantity
            try:
                inv = (
                    Inventory.objects
                    .select_for_update()
                    .get(product=item.component, warehouse=warehouse)
                )
            except Inventory.DoesNotExist:
                raise ValidationError({
                    'detail': (
                        f'No inventory for component "{item.component.name}" '
                        f'in {warehouse.name}.'
                    )
                })
            if inv.available < needed:
                raise ValidationError({
                    'detail': (
                        f'Insufficient stock for component "{item.component.name}" '
                        f'in {warehouse.name}. '
                        f'Need: {needed}, available: {inv.available}.'
                    )
                })

        # All checks passed – consume components
        for item in components:
            needed = item.quantity * quantity
            inv = (
                Inventory.objects
                .select_for_update()
                .get(product=item.component, warehouse=warehouse)
            )
            inv.quantity -= needed
            inv.save(update_fields=['quantity', 'updated_at'])

        # Increment bundle stock
        bundle_inv, _ = Inventory.objects.select_for_update().get_or_create(
            product=bundle, warehouse=warehouse, defaults={'quantity': 0},
        )
        bundle_inv.quantity += quantity
        bundle_inv.save(update_fields=['quantity', 'updated_at'])

    return bundle_inv


def assemble_kit(*, kit, warehouse, quantity: int, user=None) -> Inventory:
    """Backward-compatible alias for assemble_bundle."""
    return assemble_bundle(bundle=kit, warehouse=warehouse, quantity=quantity, user=user)


def disassemble_bundle(*, bundle, warehouse, quantity: int, user=None) -> list:
    """
    Disassemble *quantity* units of a bundle back into components.

    The bundle's Inventory is decremented and each component's Inventory
    is incremented by (bundle_item.quantity × quantity).

    Returns a list of updated component Inventory objects.
    """
    if quantity <= 0:
        raise ValidationError({'quantity': 'Must be a positive integer.'})

    if not bundle.is_bundle:
        raise ValidationError({'detail': 'Product is not a bundle.'})
    if not bundle.is_physical:
        raise ValidationError({'detail': 'Virtual products do not track inventory.'})

    components = list(ProductBundle.objects.filter(bundle=bundle).select_related('component'))
    if not components:
        raise ValidationError({'detail': 'Bundle has no components defined.'})

    with transaction.atomic():
        # Lock bundle inventory & validate
        try:
            bundle_inv = (
                Inventory.objects
                .select_for_update()
                .get(product=bundle, warehouse=warehouse)
            )
        except Inventory.DoesNotExist:
            raise ValidationError({
                'detail': f'No inventory for bundle "{bundle.name}" in {warehouse.name}.'
            })

        if bundle_inv.available < quantity:
            raise ValidationError({
                'detail': (
                    f'Insufficient bundle stock. '
                    f'Available: {bundle_inv.available}, requested: {quantity}.'
                )
            })

        # Decrement bundle
        bundle_inv.quantity -= quantity
        bundle_inv.save(update_fields=['quantity', 'updated_at'])

        # Return components to inventory
        result = []
        for item in components:
            returned = item.quantity * quantity
            comp_inv, _ = Inventory.objects.select_for_update().get_or_create(
                product=item.component, warehouse=warehouse,
                defaults={'quantity': 0},
            )
            comp_inv.quantity += returned
            comp_inv.save(update_fields=['quantity', 'updated_at'])
            result.append(comp_inv)

    return result


def disassemble_kit(*, kit, warehouse, quantity: int, user=None) -> list:
    """Backward-compatible alias for disassemble_bundle."""
    return disassemble_bundle(bundle=kit, warehouse=warehouse, quantity=quantity, user=user)

