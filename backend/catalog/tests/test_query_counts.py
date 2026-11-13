from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Merchant, Product


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
