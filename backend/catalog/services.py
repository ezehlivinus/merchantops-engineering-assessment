from django.db import transaction

from catalog.models import Inventory, InventoryAdjustment


class InventoryMissing(Exception):
    """The variant has no inventory record to adjust."""


class InsufficientStock(Exception):
    """The adjustment would take stock below the reserved quantity."""

    def __init__(self, quantity, reserved):
        super().__init__("Stock cannot fall below the reserved quantity.")
        self.quantity = quantity
        self.reserved = reserved


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
        try:
            inventory = Inventory.objects.select_for_update().get(variant=variant)
        except Inventory.DoesNotExist:
            raise InventoryMissing()

        quantity_before = inventory.quantity
        quantity_after = quantity_before + delta

        # Checked while holding the lock, so no concurrent change can make
        # it stale. The CHECK constraint on Inventory is the backstop.
        if quantity_after < inventory.reserved:
            raise InsufficientStock(quantity_before, inventory.reserved)

        inventory.quantity = quantity_after
        inventory.save(update_fields=["quantity"])

        return InventoryAdjustment.objects.create(
            variant=variant,
            actor=actor,
            delta=delta,
            reason=reason,
            quantity_before=quantity_before,
            quantity_after=quantity_after,
        )
