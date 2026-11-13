from django.test import Client, TestCase
from django.urls import reverse

from catalog.models import Inventory, InventoryAdjustment, Merchant
from catalog.tests.test_inventory_adjustment_api import (
    InventoryAdjustmentFixtureMixin,
)


class InventoryAdjustmentAuthorizationTests(InventoryAdjustmentFixtureMixin, TestCase):
    """Every request outside the operator's merchant is a 404 and changes nothing."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # A second product of the operator's own merchant.
        cls.sibling_product, cls.sibling_variant = cls.create_variant(
            cls.merchant, "STOCK-002"
        )

    def assert_not_found(self, **kwargs):
        response = self.adjust({"delta": 1, "reason": "Count"}, **kwargs)

        self.assertEqual(response.status_code, 404)
        self.assert_nothing_changed()

    def assert_nothing_changed(self):
        self.assertFalse(InventoryAdjustment.objects.exists())
        for variant in (self.variant, self.other_variant, self.sibling_variant):
            self.assertEqual(self.quantity(variant), 20)

    def test_other_merchants_variant_via_own_merchant(self):
        self.assert_not_found(product=self.other_product, variant=self.other_variant)

    def test_other_merchants_variant_via_their_merchant(self):
        self.assert_not_found(
            product=self.other_product,
            variant=self.other_variant,
            merchant=self.other_merchant,
        )

    def test_own_variant_via_non_member_merchant(self):
        self.assert_not_found(merchant=self.other_merchant)

    def test_other_merchants_variant_under_own_product(self):
        self.assert_not_found(variant=self.other_variant)

    def test_own_variant_under_other_merchants_product(self):
        self.assert_not_found(product=self.other_product)

    def test_variant_of_a_different_own_product(self):
        self.assert_not_found(variant=self.sibling_variant)

    def test_unknown_merchant(self):
        unknown = Merchant(pk=999999)
        self.assert_not_found(merchant=unknown)

    def test_membership_of_both_merchants_still_scopes_by_merchant_id(self):
        # Joining the other merchant must not let merchant A's context
        # reach merchant B's stock.
        self.other_merchant.members.add(self.user)

        self.assert_not_found(product=self.other_product, variant=self.other_variant)

    def test_invalid_body_on_foreign_variant_is_still_not_found(self):
        # Authorization runs before validation, so error details never
        # reveal anything about another merchant's variant.
        response = self.adjust(
            {"delta": 0}, product=self.other_product, variant=self.other_variant
        )

        self.assertEqual(response.status_code, 404)

    def test_missing_or_invalid_merchant_id_is_rejected(self):
        url = reverse(
            "variant-adjustments",
            kwargs={"product_pk": self.product.pk, "variant_pk": self.variant.pk},
        )
        for query in ("", "?merchant_id=", "?merchant_id=abc", "?merchant_id=0"):
            with self.subTest(query=query):
                response = self.client.post(
                    url + query,
                    data={"delta": 1, "reason": "Count"},
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 400)
                self.assertIn("merchant_id", response.data)
        self.assert_nothing_changed()

    def test_unauthenticated_request_is_rejected(self):
        self.client.logout()

        response = self.adjust({"delta": 1, "reason": "Count"})

        self.assertIn(response.status_code, (401, 403))
        self.assert_nothing_changed()

    def test_only_post_is_allowed(self):
        url = reverse(
            "variant-adjustments",
            kwargs={"product_pk": self.product.pk, "variant_pk": self.variant.pk},
        )
        url += f"?merchant_id={self.merchant.pk}"

        for method in ("get", "put", "patch", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(
                    url, content_type="application/json"
                )

                self.assertEqual(response.status_code, 405)
        self.assert_nothing_changed()


class InventoryAdjustmentCSRFTests(InventoryAdjustmentFixtureMixin, TestCase):
    """Session-authenticated writes must carry the CSRF token."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.token = self.client.get("/login/").cookies["csrftoken"].value
        self.client.force_login(self.user)

    def post(self, **headers):
        url = reverse(
            "variant-adjustments",
            kwargs={"product_pk": self.product.pk, "variant_pk": self.variant.pk},
        )
        return self.client.post(
            f"{url}?merchant_id={self.merchant.pk}",
            data={"delta": 1, "reason": "Count"},
            content_type="application/json",
            **headers,
        )

    def test_post_without_csrf_token_is_rejected(self):
        response = self.post()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Inventory.objects.get(variant=self.variant).quantity, 20)
        self.assertFalse(InventoryAdjustment.objects.exists())

    def test_post_with_csrf_token_succeeds(self):
        response = self.post(HTTP_X_CSRFTOKEN=self.token)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Inventory.objects.get(variant=self.variant).quantity, 21)
