"""Concurrency and atomicity of inventory adjustments, on real MySQL.

TransactionTestCase commits for real (TestCase would hide each test inside
one transaction that other connections cannot see). Django opens one
database connection per thread, so every worker below runs in its own
MySQL session, exactly like concurrent requests.
"""

import threading
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import DatabaseError, DataError, connection, transaction
from django.test import TransactionTestCase

from catalog.models import (
    Category,
    Inventory,
    InventoryAdjustment,
    Merchant,
    Product,
    ProductVariant,
)
from catalog.services import InsufficientStock, adjust_inventory

WORKER_TIMEOUT = 30


class Worker(threading.Thread):
    """Run a function on its own thread, and so its own DB connection."""

    def __init__(self, target, barrier=None):
        super().__init__(daemon=True)
        self.function = target
        self.barrier = barrier
        self.result = None
        self.error = None

    def run(self):
        try:
            if self.barrier:
                self.barrier.wait(timeout=WORKER_TIMEOUT)
            self.result = self.function()
        except Exception as error:
            self.error = error
        finally:
            connection.close()


class InventoryAdjustmentConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="concurrent-operator", password="test-password"
        )
        merchant = Merchant.objects.create(name="Concurrent", slug="concurrent")
        category = Category.objects.create(merchant=merchant, name="Stock")
        product = Product.objects.create(
            merchant=merchant, category=category, name="Item", sku="CONC-001"
        )
        self.variant = ProductVariant.objects.create(
            product=product, sku="CONC-001-V1", name="Variant 1"
        )

    def set_stock(self, quantity, reserved):
        Inventory.objects.create(
            variant=self.variant, quantity=quantity, reserved=reserved
        )

    def stock(self):
        return Inventory.objects.get(variant=self.variant).quantity

    def adjuster(self, delta):
        return lambda: adjust_inventory(
            variant=self.variant, actor=self.user, delta=delta, reason="Concurrent"
        )

    def run_together(self, functions):
        barrier = threading.Barrier(len(functions))
        workers = [Worker(function, barrier) for function in functions]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(WORKER_TIMEOUT)
            self.assertFalse(worker.is_alive(), "worker did not finish")
        return workers

    def test_concurrent_increments_are_not_lost(self):
        self.set_stock(quantity=20, reserved=0)

        workers = self.run_together([self.adjuster(1) for _ in range(8)])

        self.assertEqual([w.error for w in workers], [None] * 8)
        self.assertEqual(self.stock(), 28)

        # Rows are inserted while the lock is held, so id order is the order
        # the adjustments were applied: each starts where the last ended.
        adjustments = list(InventoryAdjustment.objects.order_by("id"))
        self.assertEqual(len(adjustments), 8)
        self.assertEqual(adjustments[0].quantity_before, 20)
        for previous, current in zip(adjustments, adjustments[1:]):
            self.assertEqual(current.quantity_before, previous.quantity_after)
        self.assertEqual(adjustments[-1].quantity_after, 28)

    def test_concurrent_decrements_respect_reserved_floor(self):
        # 6 units above reserved: exactly two of five "-3" requests fit.
        self.set_stock(quantity=10, reserved=4)

        workers = self.run_together([self.adjuster(-3) for _ in range(5)])

        succeeded = [w for w in workers if w.error is None]
        refused = [w for w in workers if isinstance(w.error, InsufficientStock)]
        self.assertEqual(len(succeeded), 2)
        self.assertEqual(len(refused), 3)
        self.assertEqual(self.stock(), 4)
        self.assertEqual(InventoryAdjustment.objects.count(), 2)

    def test_adjustment_waits_for_a_concurrent_writer(self):
        # Deterministic: does not depend on how threads get scheduled.
        self.set_stock(quantity=20, reserved=0)
        worker = Worker(self.adjuster(1))

        with transaction.atomic():
            # Another transaction locks the row and changes the stock.
            inventory = Inventory.objects.select_for_update().get(variant=self.variant)
            inventory.quantity = 50
            inventory.save(update_fields=["quantity"])

            worker.start()
            worker.join(1)
            # The adjustment must be blocked on the row lock, not finished.
            self.assertTrue(worker.is_alive())

        worker.join(WORKER_TIMEOUT)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(worker.error)

        # It started from the committed 50, not the 20 it could have seen.
        self.assertEqual(worker.result.quantity_before, 50)
        self.assertEqual(worker.result.quantity_after, 51)
        self.assertEqual(self.stock(), 51)


class InventoryAdjustmentAtomicityTests(TransactionTestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="atomic-operator", password="test-password"
        )
        merchant = Merchant.objects.create(name="Atomic", slug="atomic")
        category = Category.objects.create(merchant=merchant, name="Stock")
        product = Product.objects.create(
            merchant=merchant, category=category, name="Item", sku="ATOM-001"
        )
        self.variant = ProductVariant.objects.create(
            product=product, sku="ATOM-001-V1", name="Variant 1"
        )
        Inventory.objects.create(variant=self.variant, quantity=20, reserved=5)

    def assert_unchanged(self):
        self.assertEqual(Inventory.objects.get(variant=self.variant).quantity, 20)
        self.assertFalse(InventoryAdjustment.objects.exists())

    def test_stock_rolls_back_when_audit_insert_fails(self):
        with mock.patch.object(
            InventoryAdjustment.objects,
            "create",
            side_effect=DatabaseError("audit insert failed"),
        ):
            with self.assertRaisesMessage(DatabaseError, "audit insert failed"):
                adjust_inventory(
                    variant=self.variant, actor=self.user, delta=5, reason="Count"
                )

        self.assert_unchanged()

    def test_stock_rolls_back_when_database_rejects_audit_row(self):
        # No mock: the serializer would stop this reason, so call the
        # service directly. MySQL strict mode rejects the audit INSERT,
        # which runs after the stock UPDATE.
        with self.assertRaises(DataError):
            adjust_inventory(
                variant=self.variant, actor=self.user, delta=5, reason="x" * 300
            )

        self.assert_unchanged()
