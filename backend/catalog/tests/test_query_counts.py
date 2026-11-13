from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import (
    Availability,
    Category,
    Inventory,
    Merchant,
    PriceRule,
    Product,
    ProductVariant,
)


class ProductListQueryCountTests(TestCase):
    """The list must cost the same number of queries for any page size."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="query-count-user",
            password="test-password",
        )
        cls.merchant = Merchant.objects.create(
            name="Query Count Merchant",
            slug="query-count-merchant",
        )
        cls.merchant.members.add(cls.user)

        categories = [
            Category.objects.create(merchant=cls.merchant, name=f"Category {n}")
            for n in range(3)
        ]
        Product.objects.bulk_create(
            [
                Product(
                    merchant=cls.merchant,
                    category=categories[n % 3],
                    name=f"Product {n}",
                    sku=f"QC-{n:03d}",
                )
                for n in range(30)
            ]
        )

    def setUp(self):
        self.client.force_login(self.user)

    def get_list(self, **params):
        return self.client.get(
            reverse("product-list"),
            {"merchant_id": self.merchant.pk, **params},
        )

    def test_full_page_uses_constant_queries(self):
        # session, user, merchant membership, count, products with category.
        with self.assertNumQueries(5):
            response = self.get_list()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 25)
        self.assertEqual(response.data["results"][0]["category"], "Category 0")

    def test_search_uses_constant_queries(self):
        with self.assertNumQueries(5):
            response = self.get_list(search="Product 1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 11)


class ProductEditorQueryCountTests(TestCase):
    """The editor must cost the same number of queries for any variant count."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="editor-query-user",
            password="test-password",
        )
        cls.merchant = Merchant.objects.create(
            name="Editor Query Merchant",
            slug="editor-query-merchant",
        )
        cls.merchant.members.add(cls.user)
        category = Category.objects.create(merchant=cls.merchant, name="Tools")

        cls.small = cls.create_product(category, "SMALL", variants=1)
        cls.large = cls.create_product(category, "LARGE", variants=6)

    @classmethod
    def create_product(cls, category, sku, variants):
        product = Product.objects.create(
            merchant=cls.merchant, category=category, name=sku, sku=sku
        )
        Availability.objects.create(product=product, channel="online")

        for number in range(variants):
            variant = ProductVariant.objects.create(
                product=product, sku=f"{sku}-V{number}", name=f"Variant {number}"
            )
            PriceRule.objects.create(variant=variant, tier="retail", amount=10)
            PriceRule.objects.create(variant=variant, tier="wholesale", amount=8)
            Inventory.objects.create(variant=variant, quantity=10, reserved=2)

        return product

    def setUp(self):
        self.client.force_login(self.user)

    def get_editor(self, product):
        return self.client.get(
            reverse("product-editor", kwargs={"pk": product.pk}),
            {"merchant_id": self.merchant.pk},
        )

    def test_query_count_does_not_grow_with_variants(self):
        # session, user, merchant, product + category, variants + inventory,
        # prices, availability.
        for product, variant_count in ((self.small, 1), (self.large, 6)):
            with self.subTest(variants=variant_count):
                with self.assertNumQueries(7):
                    response = self.get_editor(product)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(response.data["variants"]), variant_count)

    def test_prices_and_inventory_are_attached_to_their_variant(self):
        response = self.get_editor(self.large)

        variant = response.data["variants"][3]

        self.assertEqual(variant["sku"], "LARGE-V3")
        self.assertEqual(
            [(price["tier"], price["amount"]) for price in variant["prices"]],
            [("retail", "10.00"), ("wholesale", "8.00")],
        )
        self.assertEqual(variant["inventory"]["quantity"], 10)
        self.assertEqual(variant["inventory"]["reserved"], 2)

    def test_variant_without_inventory_reports_none(self):
        ProductVariant.objects.create(
            product=self.small, sku="SMALL-NOINV", name="No inventory"
        )

        response = self.get_editor(self.small)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["variants"][-1]["inventory"])
