from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management import call_command
from django.test import TestCase

from catalog.models import Merchant


class MeasureEndpointsCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Six products: the demo merchant owns ASSESS-000001, -03 and -05.
        call_command("seed_catalog", products=6, seed=42, verbosity=0)
        call_command("seed_complex_products", verbosity=0)

        user = get_user_model().objects.create_user(
            username="demo_operator",
            password="test-password",
        )
        Merchant.objects.get(slug="assessment-merchant-01").members.add(user)

    def test_command_reports_every_case(self):
        output = StringIO()

        call_command("measure_endpoints", runs=1, stdout=output)

        report = output.getvalue()

        for label in (
            "GET list, page 1",
            "GET editor, 50 variants",
            "Workflow: catalogue page load",
            "Workflow: open product, 50 variants",
        ):
            self.assertIn(label, report)

    def test_command_leaves_no_session_behind(self):
        call_command("measure_endpoints", runs=1, stdout=StringIO())

        self.assertEqual(Session.objects.count(), 0)
