"""API documentation.

These assert the schema is reachable, public, complete and warning-free, so a broken
annotation fails the test suite rather than quietly producing a misleading page.
"""

from io import StringIO

import yaml
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase


class DocsEndpointTests(APITestCase):
    def test_swagger_ui_is_public(self):
        response = self.client.get(reverse("swagger-ui"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_schema_is_public(self):
        response = self.client.get(reverse("schema"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_schema_documents_the_claim_route(self):
        body = self.client.get(reverse("schema")).content.decode()
        self.assertIn("/api/missions/{id}/claim/", body)

    def test_schema_documents_every_route(self):
        body = self.client.get(reverse("schema")).content.decode()
        for path in (
            "/api/auth/register/",
            "/api/auth/token/",
            "/api/hostels/",
            "/api/hostels/{id}/",
            "/api/participants/",
            "/api/participants/me/",
            "/api/participants/{id}/",
            "/api/missions/",
            "/api/missions/{id}/",
            "/api/missions/{id}/claim/",
            "/api/missions/{id}/status/",
            "/api/leaderboard/",
        ):
            with self.subTest(path=path):
                self.assertIn(path, body)


class SchemaContentTests(TestCase):
    """Parses the generated schema rather than grepping it."""

    @classmethod
    def setUpTestData(cls):
        out = StringIO()
        call_command("spectacular", "--validate", "--fail-on-warn", stdout=out)
        cls.schema = yaml.safe_load(out.getvalue())

    def test_generation_is_warning_free(self):
        """`call_command(... --fail-on-warn)` above raises if anything warns."""
        self.assertIn("paths", self.schema)

    def test_title_and_version(self):
        self.assertEqual(self.schema["info"]["title"], "Operation Golden Keyboard API")
        self.assertEqual(self.schema["info"]["version"], "1.0.0")

    def test_token_auth_is_offered_as_a_security_scheme(self):
        schemes = self.schema["components"]["securitySchemes"]
        self.assertIn("tokenAuth", schemes)
        self.assertEqual(schemes["tokenAuth"]["in"], "header")
        self.assertEqual(schemes["tokenAuth"]["name"], "Authorization")

    def test_claim_documents_every_failure_mode(self):
        responses = self.schema["paths"]["/api/missions/{id}/claim/"]["post"]["responses"]
        self.assertEqual(
            set(responses), {"200", "400", "403", "404", "409", "429"}
        )

    def test_claim_documents_no_request_body(self):
        """The body is ignored, so the schema must not invite one."""
        operation = self.schema["paths"]["/api/missions/{id}/claim/"]["post"]
        self.assertNotIn("requestBody", operation)

    def test_status_route_accepts_only_participant_writable_statuses(self):
        operation = self.schema["paths"]["/api/missions/{id}/status/"]["patch"]
        ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        component = self.schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        enum_ref = component["properties"]["status"]["$ref"].rsplit("/", 1)[-1]
        self.assertEqual(
            set(self.schema["components"]["schemas"][enum_ref]["enum"]),
            {"in_progress", "cracked"},
        )

    def test_status_route_documents_its_failure_modes(self):
        responses = self.schema["paths"]["/api/missions/{id}/status/"]["patch"]["responses"]
        self.assertEqual(set(responses), {"200", "400", "403", "404"})

    def test_status_route_documents_both_of_its_400_codes(self):
        """A status change after the deadline is the task's own example of a refusal,
        so the docs must show ``mission_expired`` alongside ``cracked_is_final``."""
        refusal = self.schema["paths"]["/api/missions/{id}/status/"]["patch"]["responses"]["400"]
        examples = refusal["content"]["application/json"]["examples"]
        self.assertEqual(
            {example["value"]["code"] for example in examples.values()},
            {"mission_expired", "cracked_is_final"},
        )

    def test_mission_list_documents_its_query_parameters(self):
        params = self.schema["paths"]["/api/missions/"]["get"]["parameters"]
        names = {p["name"] for p in params}
        for expected in ("status", "difficulty", "hostel", "search", "ordering", "page"):
            with self.subTest(parameter=expected):
                self.assertIn(expected, names)

    def test_the_status_parameter_lists_every_effective_status(self):
        params = self.schema["paths"]["/api/missions/"]["get"]["parameters"]
        status_param = next(p for p in params if p["name"] == "status")
        self.assertEqual(
            set(status_param["schema"]["enum"]),
            {"unclaimed", "in_progress", "cracked", "expired"},
        )

    def test_public_routes_carry_no_auth_requirement(self):
        """There is no root-level `security`, so an absent one means 'public'."""
        self.assertNotIn("security", self.schema)

        for path, method in (
            ("/api/leaderboard/", "get"),
            ("/api/auth/register/", "post"),
        ):
            with self.subTest(path=path):
                self.assertFalse(
                    self.schema["paths"][path][method].get("security"),
                    "public route must not require a security scheme",
                )

    def test_protected_routes_do_require_a_token(self):
        for path, method in (("/api/missions/", "get"), ("/api/participants/me/", "get")):
            with self.subTest(path=path):
                security = self.schema["paths"][path][method]["security"]
                self.assertIn({"tokenAuth": []}, security)

    def test_register_documents_the_token_in_its_response(self):
        operation = self.schema["paths"]["/api/auth/register/"]["post"]
        ref = operation["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
        component = self.schema["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        self.assertEqual(set(component["properties"]), {"participant", "token"})
