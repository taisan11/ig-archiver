from __future__ import annotations

import http.client
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from ig_archiver.viewer import _safe_media, create_handler


class ViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.archive = root / "archive"
        self.archive.mkdir()
        self.db = root / "state.sqlite3"
        self.photo = self.archive / "example.jpg"
        self.photo.write_bytes(b"0123456789")
        self.outside = root / "secret.jpg"
        self.outside.write_bytes(b"secret")
        with sqlite3.connect(self.db) as con:
            con.executescript(
                """CREATE TABLE seen (
                    kind TEXT NOT NULL, item_key TEXT NOT NULL, username TEXT,
                    archived_at TEXT NOT NULL, path TEXT, PRIMARY KEY(kind, item_key)
                );"""
            )
            con.executemany(
                "INSERT INTO seen VALUES (?,?,?,?,?)",
                [
                    ("post", "a", "alice", "2026-11-07T10:00:00+00:00", str(self.photo)),
                    ("story", "b", "<script>alert(1)</script>", "2026-11-07T09:00:00+00:00", str(self.outside)),
                ],
            )
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), create_handler(self.db, self.archive)
        )
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, path, method="GET", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port)
        try:
            conn.request(method, path, headers=headers or {})
            res = conn.getresponse()
            return res.status, dict(res.getheaders()), res.read()
        finally:
            conn.close()

    def test_browse_escapes_usernames_and_filters(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn(b"&lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn(b"<script>", body)
        self.assertIn("no-store", headers["Cache-Control"])
        status, _, body = self.request("/?kind=post&user=alice")
        self.assertEqual(status, 200)
        self.assertIn("1 件".encode(), body)
        self.assertNotIn(b"&lt;script&gt;", body)

    def test_media_range_and_head(self):
        status, headers, body = self.request(
            "/media?kind=post&id=a", headers={"Range": "bytes=3-5"}
        )
        self.assertEqual((status, body), (206, b"345"))
        self.assertEqual(headers["Content-Range"], "bytes 3-5/10")
        status, headers, body = self.request("/media?kind=post&id=a", method="HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(headers["Content-Length"], "10")
        self.assertEqual(
            self.request("/media?kind=post&id=a", headers={"Range": "bytes=100-"})[0],
            416,
        )

    def test_outside_and_unknown_files_not_served(self):
        self.assertIsNone(_safe_media(str(self.outside), self.archive))
        self.assertEqual(self.request("/media?kind=story&id=b")[0], 404)
        self.assertEqual(self.request("/media?kind=post&id=missing")[0], 404)
        self.assertEqual(self.request("/media?kind=bogus&id=a")[0], 404)
        self.assertEqual(
            self.request("/media?kind=post&id=a", headers={"Host": "evil.example"})[0],
            403,
        )
        self.assertEqual(self.request("/", method="POST")[0], 501)


if __name__ == "__main__":
    unittest.main()
