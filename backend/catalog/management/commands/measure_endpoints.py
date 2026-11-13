"""Report query counts and timings for the main operator workflows.

The command replays read-only API requests in-process (Django test client)
against the current database, signed in as an existing operator. It never
writes catalogue data, so it can be rerun before and after a change and the
two outputs compared line by line.

    python manage.py measure_endpoints --runs 20
"""

import hashlib
import math
import statistics
import time

import django
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, reset_queries
from django.test import Client
from django.test.utils import CaptureQueriesContext

from catalog.models import Inventory, Merchant, PriceRule, Product, ProductVariant

# Seeded products owned by the demo merchant (see seed_complex_products).
SIMPLE_SKU = "ASSESS-000005"  # 2 variants
COMPLEX_SKU = "ASSESS-000001"  # 50 variants
SEARCH_TERM = "0012"
PAGE_SIZE = 25


class Command(BaseCommand):
    help = "Measure query counts and timings for key API workflows."

    def add_arguments(self, parser):
        parser.add_argument("--runs", type=int, default=20)
        parser.add_argument("--username", default="demo_operator")
        parser.add_argument("--merchant-slug", default="assessment-merchant-01")

    def handle(self, *args, **options):
        runs = options["runs"]
        if runs < 1:
            raise CommandError("--runs must be at least 1.")

        user = get_user_model().objects.filter(username=options["username"]).first()
        if user is None:
            raise CommandError("Operator not found. Run setup.sh first.")

        merchant = Merchant.objects.filter(slug=options["merchant_slug"]).first()
        if merchant is None:
            raise CommandError("Merchant not found. Run seed_catalog first.")

        self.merchant_id = merchant.pk
        products = Product.objects.filter(merchant=merchant)
        simple = products.filter(sku=SIMPLE_SKU).first()
        complex_product = products.filter(sku=COMPLEX_SKU).first()
        if simple is None or complex_product is None:
            raise CommandError("Expected seeded products are missing.")

        last_page = math.ceil(products.count() / PAGE_SIZE)

        # The test client's default host ("testserver") is only allowed under
        # the test runner; outside it, DEBUG=True allows "localhost".
        host = "testserver" if "testserver" in settings.ALLOWED_HOSTS else "localhost"
        self.client = Client(SERVER_NAME=host)
        self.client.force_login(user)

        cases = [
            # Single endpoints.
            ("GET list, page 1", lambda: [self.get("")]),
            (
                f"GET list, last page ({last_page})",
                lambda: [self.get("", page=last_page)],
            ),
            (
                f'GET list, search "{SEARCH_TERM}"',
                lambda: [self.get("", search=SEARCH_TERM)],
            ),
            ("GET detail", lambda: [self.get(f"{simple.pk}/")]),
            (
                "GET editor, 2 variants",
                lambda: [self.get(f"{simple.pk}/editor/")],
            ),
            (
                "GET editor, 50 variants",
                lambda: [self.get(f"{complex_product.pk}/editor/")],
            ),
            (
                "GET activity",
                lambda: [self.get(f"{complex_product.pk}/activity/")],
            ),
            # Workflows: the requests the Vue app issues for one screen.
            ("Workflow: catalogue page load", self.catalogue_page_load),
            (
                "Workflow: open product, 50 variants",
                lambda: self.open_product(complex_product.pk),
            ),
        ]

        try:
            rows = [self.measure(label, steps, runs) for label, steps in cases]
        finally:
            # Remove the session row created by force_login.
            self.client.logout()

        self.print_report(rows, runs)

    # -- Workflows (keep in step with the frontend) ------------------------

    def catalogue_page_load(self):
        """Mirror CatalogueView.vue fetchPage()."""
        # Until measurement 01 the view also fetched each row's detail;
        # see measurements/00-baseline.md for that request sequence.
        return [self.get("")]

    def open_product(self, product_id):
        """Mirror EditorView.vue readEditor()."""
        return [
            self.get(f"{product_id}/editor/"),
            self.get(f"{product_id}/"),
        ]

    # -- Measurement --------------------------------------------------------

    def get(self, path, **params):
        response = self.client.get(
            "/api/products/" + path,
            {"merchant_id": self.merchant_id, **params},
        )
        if response.status_code != 200:
            raise CommandError(f"GET {path} returned {response.status_code}.")
        return response

    def measure(self, label, steps, runs):
        steps()  # Warm-up run, not recorded.

        timings = []
        query_counts = set()

        for _ in range(runs):
            # With DEBUG=True the query log is a bounded queue; clear it so
            # the captured count is exact on every run.
            reset_queries()

            with CaptureQueriesContext(connection) as captured:
                started = time.perf_counter()
                responses = steps()
                elapsed = time.perf_counter() - started

            timings.append(elapsed * 1000)
            query_counts.add(len(captured))

        if len(query_counts) != 1:
            raise CommandError(f"Unstable query count for {label}: {query_counts}")

        body = b"".join(response.content for response in responses)
        timings.sort()

        return {
            "label": label,
            "requests": len(responses),
            "queries": query_counts.pop(),
            "median": statistics.median(timings),
            "p95": timings[math.ceil(0.95 * len(timings)) - 1],
            "bytes": len(body),
            "sha": hashlib.sha256(body).hexdigest()[:12],
        }

    def print_report(self, rows, runs):
        write = self.stdout.write

        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION(), @@transaction_isolation")
            mysql_version, isolation = cursor.fetchone()

        write(
            f"Django {django.get_version()}, MySQL {mysql_version}, "
            f"isolation {isolation}, DEBUG={settings.DEBUG}, runs={runs} "
            "(plus 1 warm-up), in-process test client."
        )
        write(
            f"Dataset: {Product.objects.count()} products, "
            f"{ProductVariant.objects.count()} variants, "
            f"{PriceRule.objects.count()} price rules, "
            f"{Inventory.objects.count()} inventory rows."
        )
        write("")
        write(
            "| Case | Requests | Queries | Median ms | p95 ms | Bytes | "
            "Body SHA-256 |"
        )
        write("| --- | ---: | ---: | ---: | ---: | ---: | --- |")

        for row in rows:
            write(
                f"| {row['label']} | {row['requests']} | {row['queries']} | "
                f"{row['median']:.1f} | {row['p95']:.1f} | {row['bytes']} | "
                f"`{row['sha']}` |"
            )
