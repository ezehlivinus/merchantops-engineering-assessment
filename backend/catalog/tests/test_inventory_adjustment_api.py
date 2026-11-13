from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from catalog.models import (
    Category,
    Inventory,
    InventoryAdjustment,
    Merchant,
    Product,
    ProductVariant,
)


class InventoryAdjustmentFixtureMixin:
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="stock-operator",
            password="test-password",
        )
        cls.merchant = Merchant.objects.create(
            name="Stock Merchant",
            slug="stock-merchant",
        )
        cls.other_merchant = Merchant.objects.create(
            name="Other Stock Merchant",
            slug="other-stock-merchant",
        )
        cls.merchant.members.add(cls.user)

        cls.product, cls.variant = cls.create_variant(cls.merchant, "STOCK-001")
        cls.other_product, cls.other_variant = cls.create_variant(
            cls.other_merchant, "PRIVATE-001"
        )

    @classmethod
    def create_variant(cls, merchant, sku, quantity=20, reserved=5):
        category = Category.objects.create(merchant=merchant, name=f"{sku} category")
        product = Product.objects.create(
            merchant=merchant, category=category, name=sku, sku=sku
        )
        variant = ProductVariant.objects.create(
            product=product, sku=f"{sku}-V1", name="Variant 1"
        )
        Inventory.objects.create(variant=variant, quantity=quantity, reserved=reserved)
        return product, variant

    def setUp(self):
        self.client.force_login(self.user)

    def adjust(self, data, product=None, variant=None, merchant=None):
        product = product or self.product
        variant = variant or self.variant
        merchant = merchant or self.merchant
        url = reverse(
            "variant-adjustments",
            kwargs={"product_pk": product.pk, "variant_pk": variant.pk},
        )
        return self.client.post(
            f"{url}?merchant_id={merchant.pk}",
            data=data,
            content_type="application/json",
        )

    def quantity(self, variant=None):
        return Inventory.objects.get(variant=variant or self.variant).quantity


class InventoryAdjustmentAPITests(InventoryAdjustmentFixtureMixin, TestCase):
    def test_increase_updates_stock_and_records_audit(self):
        response = self.adjust({"delta": 7, "reason": "Found in back room"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.quantity(), 27)

        adjustment = InventoryAdjustment.objects.get()
        self.assertEqual(adjustment.variant, self.variant)
        self.assertEqual(adjustment.actor, self.user)
        self.assertEqual(adjustment.delta, 7)
        self.assertEqual(adjustment.reason, "Found in back room")
        self.assertEqual(adjustment.quantity_before, 20)
        self.assertEqual(adjustment.quantity_after, 27)
        self.assertIsNotNone(adjustment.created_at)

        self.assertEqual(
            response.data,
            {
                "id": adjustment.pk,
                "variant": self.variant.pk,
                "delta": 7,
                "reason": "Found in back room",
                "quantity_before": 20,
                "quantity_after": 27,
                "actor": "stock-operator",
                "created_at": response.data["created_at"],
            },
        )

    def test_decrease_updates_stock(self):
        response = self.adjust({"delta": -4, "reason": "Damaged in transit"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.quantity(), 16)
        self.assertEqual(response.data["quantity_before"], 20)
        self.assertEqual(response.data["quantity_after"], 16)

    def test_reserved_quantity_is_not_changed(self):
        self.adjust({"delta": -4, "reason": "Cycle count"})

        self.assertEqual(Inventory.objects.get(variant=self.variant).reserved, 5)

    def test_successive_adjustments_chain_quantities(self):
        self.adjust({"delta": 3, "reason": "First"})
        self.adjust({"delta": -2, "reason": "Second"})

        first, second = InventoryAdjustment.objects.order_by("id")

        self.assertEqual(first.quantity_after, second.quantity_before)
        self.assertEqual(self.quantity(), 21)

    def test_other_merchants_variant_is_not_found(self):
        response = self.adjust(
            {"delta": 5, "reason": "Tampering"},
            product=self.other_product,
            variant=self.other_variant,
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.quantity(self.other_variant), 20)
        self.assertFalse(InventoryAdjustment.objects.exists())

    def test_unauthenticated_request_is_rejected(self):
        self.client.logout()

        response = self.adjust({"delta": 5, "reason": "Anonymous"})

        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(self.quantity(), 20)
        self.assertFalse(InventoryAdjustment.objects.exists())


class InventoryAdjustmentConstraintTests(InventoryAdjustmentFixtureMixin, TestCase):
    def create_adjustment(self, **values):
        with transaction.atomic():
            return InventoryAdjustment.objects.create(
                variant=self.variant, actor=self.user, reason="Test", **values
            )

    def test_zero_delta_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError):
            self.create_adjustment(delta=0, quantity_before=5, quantity_after=5)

    def test_inconsistent_quantities_are_rejected_by_database(self):
        with self.assertRaises(IntegrityError):
            self.create_adjustment(delta=-2, quantity_before=5, quantity_after=4)

    def test_delta_larger_than_quantity_before_is_rejected_by_database(self):
        # 2 + (-5) is negative; MySQL reports a CHECK violation rather than
        # an unsigned out-of-range error.
        with self.assertRaises(IntegrityError):
            self.create_adjustment(delta=-5, quantity_before=2, quantity_after=0)

    def test_negative_delta_with_consistent_quantities_is_accepted(self):
        # quantity_before is UNSIGNED and delta is signed: make sure MySQL
        # evaluates the CHECK correctly for a valid decrease.
        adjustment = self.create_adjustment(
            delta=-5, quantity_before=5, quantity_after=0
        )

        self.assertIsNotNone(adjustment.pk)


class InventoryAdjustmentValidationTests(InventoryAdjustmentFixtureMixin, TestCase):
    """Rejected requests return an error and change nothing."""

    def assert_rejected(self, data, status_code=400, field=None):
        response = self.adjust(data)

        self.assertEqual(response.status_code, status_code, response.data)
        if field:
            self.assertIn(field, response.data)
        self.assertEqual(self.quantity(), 20)
        self.assertFalse(InventoryAdjustment.objects.exists())
        return response

    def test_invalid_deltas_are_rejected(self):
        for delta in (0, "abc", 1.5, "1.5", None, True, "", 2**31):
            with self.subTest(delta=delta):
                self.assert_rejected({"delta": delta, "reason": "Count"}, field="delta")

    def test_missing_delta_is_rejected(self):
        self.assert_rejected({"reason": "Count"}, field="delta")

    def test_zero_delta_message(self):
        response = self.assert_rejected({"delta": 0, "reason": "Count"})

        self.assertEqual(response.data["delta"], ["Must be a nonzero whole number."])

    def test_invalid_reasons_are_rejected(self):
        for reason in ("", "   ", None, "x" * 256):
            with self.subTest(reason=reason):
                self.assert_rejected({"delta": 1, "reason": reason}, field="reason")

    def test_missing_reason_is_rejected(self):
        self.assert_rejected({"delta": 1}, field="reason")

    def test_reason_is_trimmed_and_may_use_full_length(self):
        response = self.adjust({"delta": 1, "reason": "  " + "x" * 255 + "  "})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(InventoryAdjustment.objects.get().reason, "x" * 255)

    def test_adjusting_down_to_reserved_is_allowed(self):
        response = self.adjust({"delta": -15, "reason": "Count"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.quantity(), 5)

    def test_adjusting_below_reserved_is_a_conflict(self):
        response = self.assert_rejected({"delta": -16, "reason": "Count"}, 409)

        self.assertEqual(response.data["quantity"], 20)
        self.assertEqual(response.data["reserved"], 5)
        self.assertIn("at most 15 can be removed", response.data["detail"])

    def test_variant_without_inventory_is_a_conflict(self):
        variant = ProductVariant.objects.create(
            product=self.product, sku="STOCK-001-NOINV", name="No inventory"
        )

        response = self.adjust({"delta": 1, "reason": "Count"}, variant=variant)

        self.assertEqual(response.status_code, 409)
        self.assertFalse(InventoryAdjustment.objects.exists())
        self.assertFalse(Inventory.objects.filter(variant=variant).exists())


class InventoryAdjustmentHistoryTests(InventoryAdjustmentFixtureMixin, TestCase):
    def history(self, product=None, variant=None, merchant=None, **params):
        product = product or self.product
        variant = variant or self.variant
        merchant = merchant or self.merchant
        url = reverse(
            "variant-adjustments",
            kwargs={"product_pk": product.pk, "variant_pk": variant.pk},
        )
        return self.client.get(url, {"merchant_id": merchant.pk, **params})

    def test_lists_adjustments_newest_first(self):
        self.adjust({"delta": 3, "reason": "First"})
        self.adjust({"delta": -2, "reason": "Second"})

        response = self.history()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        latest, earliest = response.data["results"]
        self.assertEqual(latest["reason"], "Second")
        self.assertEqual(latest["delta"], -2)
        self.assertEqual(latest["quantity_before"], 23)
        self.assertEqual(latest["quantity_after"], 21)
        self.assertEqual(latest["actor"], "stock-operator")
        self.assertEqual(earliest["reason"], "First")

    def test_only_this_variants_adjustments_are_listed(self):
        _, sibling = self.create_variant(self.merchant, "STOCK-003")
        self.adjust({"delta": 1, "reason": "Mine"})
        InventoryAdjustment.objects.create(
            variant=sibling,
            actor=self.user,
            delta=1,
            reason="Sibling",
            quantity_before=20,
            quantity_after=21,
        )

        response = self.history()

        self.assertEqual([r["reason"] for r in response.data["results"]], ["Mine"])

    def test_history_is_paginated_with_constant_queries(self):
        InventoryAdjustment.objects.bulk_create(
            InventoryAdjustment(
                variant=self.variant,
                actor=self.user,
                delta=1,
                reason=f"Count {n}",
                quantity_before=20 + n,
                quantity_after=21 + n,
            )
            for n in range(30)
        )

        # session, user, merchant, variant, count, page with actors.
        with self.assertNumQueries(6):
            response = self.history()

        self.assertEqual(response.data["count"], 30)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertEqual(len(self.history(page=2).data["results"]), 5)

    def test_other_merchants_history_is_not_found(self):
        for kwargs in (
            {"product": self.other_product, "variant": self.other_variant},
            {
                "product": self.other_product,
                "variant": self.other_variant,
                "merchant": self.other_merchant,
            },
            {"merchant": self.other_merchant},
        ):
            with self.subTest(**{k: v.pk for k, v in kwargs.items()}):
                self.assertEqual(self.history(**kwargs).status_code, 404)

    def test_unauthenticated_history_is_rejected(self):
        self.client.logout()

        self.assertIn(self.history().status_code, (401, 403))
