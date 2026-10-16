import json
import unittest
from datetime import datetime
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from serve import SimHandler


class SchemaDeprecationTests(unittest.TestCase):
    deprecated_paths = (
        "/", "/schema", "/schema.json",
        "/v1.3", "/v1.3/", "/v1.3/schema", "/v1.3/schema.json",
    )
    current_paths = (
        "/v1.4", "/v1.4/", "/v1.4/schema", "/v1.4/schema.json",
    )

    @classmethod
    def setUpClass(cls):
        class Handler(SimHandler):
            serve_dir = str(Path(__file__).resolve().parent)

            def log_message(self, fmt, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def request(self, path, method="GET", headers=None):
        connection = HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def assert_deprecation_headers(self, headers):
        self.assertEqual(headers["Deprecation"], "@1792169539")
        self.assertEqual(
            headers["Link"],
            '</v1.4/>; rel="successor-version"; type="application/json"',
        )
        self.assertNotIn("Sunset", headers)
        self.assertNotIn("Location", headers)
        self.assertEqual(headers["Access-Control-Expose-Headers"], "*")

    def test_deprecated_schema_aliases(self):
        for path in self.deprecated_paths:
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 200)
                self.assertEqual(headers["Content-Type"], "application/json")
                self.assert_deprecation_headers(headers)
                schema = json.loads(body)
                self.assertEqual(schema["x-schema-status"], "deprecated")
                self.assertIn("deprecated", schema["description"])
                self.assertIn("/v1.4/", schema["description"])
                self.assertEqual(schema["x-replaced-by"], "/v1.4/")
                since = datetime.fromisoformat(schema["x-deprecated-since"])
                self.assertEqual(headers["Deprecation"], f"@{int(since.timestamp())}")
                self.assertIn("1.3.2", schema["properties"]["openrpc"]["enum"])

    def test_replacement_resolves_to_current_schema(self):
        _, _, body = self.request("/v1.3/")
        replacement = json.loads(body)["x-replaced-by"]
        status, headers, body = self.request(replacement)
        self.assertEqual(status, 200)
        self.assertNotIn("Deprecation", headers)
        self.assertNotIn("x-schema-status", json.loads(body))

    def test_current_schema_aliases_are_not_deprecated(self):
        for path in self.current_paths:
            with self.subTest(path=path):
                status, headers, _ = self.request(path)
                self.assertEqual(status, 200)
                self.assertNotIn("Deprecation", headers)
                self.assertNotIn("Link", headers)

    def test_head_preserves_headers_without_body(self):
        for path in self.deprecated_paths:
            with self.subTest(path=path):
                _, get_headers, get_body = self.request(path)
                status, headers, body = self.request(path, method="HEAD")
                self.assertEqual(status, 200)
                self.assertEqual(body, b"")
                self.assertEqual(int(headers["Content-Length"]), len(get_body))
                self.assertEqual(headers["ETag"], get_headers["ETag"])
                self.assert_deprecation_headers(headers)

    def test_not_modified_preserves_deprecation_headers(self):
        for path in self.deprecated_paths:
            with self.subTest(path=path):
                _, original_headers, _ = self.request(path)
                status, headers, body = self.request(
                    path, headers={"If-None-Match": original_headers["ETag"]},
                )
                self.assertEqual(status, 304)
                self.assertEqual(body, b"")
                self.assertEqual(headers["ETag"], original_headers["ETag"])
                self.assert_deprecation_headers(headers)

    def test_current_not_modified_has_no_deprecation(self):
        _, original_headers, _ = self.request("/v1.4/")
        status, headers, body = self.request(
            "/v1.4/", headers={"If-None-Match": original_headers["ETag"]},
        )
        self.assertEqual(status, 304)
        self.assertEqual(body, b"")
        self.assertNotIn("Deprecation", headers)
        self.assertNotIn("Link", headers)


class SchemaMetadataTests(unittest.TestCase):
    def setUp(self):
        self.handler = SimHandler.__new__(SimHandler)

    def test_missing_or_invalid_metadata_is_ignored(self):
        for body in (b"", b"not json", b"\xff", b"[]", b"null", b"{}"):
            with self.subTest(body=body):
                self.assertEqual(self.handler._schema_deprecation_headers(body), {})

    def test_invalid_or_naive_date_is_not_published(self):
        for since in (None, "invalid", "2026-10-16T00:00:00"):
            with self.subTest(since=since):
                body = json.dumps({
                    "x-schema-status": "deprecated",
                    "x-deprecated-since": since,
                    "x-replaced-by": "/v1.4/",
                })
                headers = self.handler._schema_deprecation_headers(body)
                self.assertNotIn("Deprecation", headers)
                self.assertIn("Link", headers)


if __name__ == "__main__":
    unittest.main()
