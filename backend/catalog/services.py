from django.db import transaction

from catalog.models import Inventory, InventoryAdjustment


def adjust_inventory(*, variant, actor, delta, reason):
    """Change a variant's stock by ``delta`` and record who did it and why.

    The inventory row is locked for the whole transaction, so concurrent
    adjustments to the same variant run one after another and each one
    starts from the quantity the previous one committed. The stock change
    and its audit record commit together or not at all.
    """
    with transaction.atomic():
        # SELECT ... FOR UPDATE: blocks other writers to this row until we
        # commit, and always reads the latest committed quantity.
        inventory = Inventory.objects.select_for_update().get(variant=variant)

        quantity_before = inventory.quantity
        inventory.quantity = quantity_before + delta
        inventory.save(update_fields=["quantity"])

        return InventoryAdjustment.objects.create(
            variant=variant,
            actor=actor,
            delta=delta,
            reason=reason,
            quantity_before=quantity_before,
            quantity_after=inventory.quantity,
        )
