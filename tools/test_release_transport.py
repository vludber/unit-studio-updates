import base64
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import release_transport as transport


def signed_fixture(version, text, picture, indent=None):
    # Deliberately synthetic signature: these tests exercise exact byte transport,
    # not signing. Production packages must already pass the updater's verifier.
    payload = json.dumps({"format": 1, "version": version, "files": {
        "app.js": {"data": text}, "image.webp": {"data": picture}}},
        ensure_ascii=False, indent=indent).encode()
    return json.dumps({"format": 2, "payload": base64.b64encode(payload).decode(),
                       "signature": base64.b64encode(bytes(64)).decode()}, indent=2).encode()


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "updates").mkdir()
        self.base = self.root / "updates/1.0.0.json"
        self.target = self.root / "target.json"
        self.picture = "abcdef0123456789" * 20000
        self.base.write_bytes(signed_fixture("1.0.0", "old", self.picture))
        self.target.write_bytes(signed_fixture("1.1.0", "Привет 👋\n" * 2000, self.picture))
        (self.root / "updates/latest.json").write_text('{"version":"1.0.0"}')

    def prepare(self, latest=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return transport.prepare(self.root, self.base, self.target, latest)

    def latest(self):
        path = self.root / "next-latest.json"
        path.write_bytes(transport.pack({"version": "1.1.0",
            "url": "https://raw.githubusercontent.com/vludber/unit-studio-updates/main/updates/1.1.0.json",
            "digest": "sha256:" + transport.sha(self.target.read_bytes())}))
        return path

    def test_exact_unicode_roundtrip_and_small_files(self):
        self.prepare()
        self.assertEqual(transport.assemble(self.root)[0], self.target.read_bytes())
        files = list((self.root / "publishing").rglob("*"))
        sizes = [p.stat().st_size for p in files if p.is_file()]
        self.assertLessEqual(max(sizes), transport.LIMIT)
        self.assertLess(sum(sizes), len(self.target.read_bytes()) // 4)
        current = (self.root / "updates/latest.json").read_bytes()
        transport.apply(self.root)
        self.assertEqual((self.root / "updates/latest.json").read_bytes(), current)
        self.assertFalse((self.root / "updates/1.1.0.json").exists())

    def test_changed_formatting_preserved(self):
        self.target.write_bytes(signed_fixture("1.1.0", "new", self.picture, indent=2))
        self.prepare()
        self.assertEqual(transport.assemble(self.root)[0], self.target.read_bytes())

    def test_corrupt_chunk_fails(self):
        request = self.prepare()
        chunk = self.root / "publishing/chunks" / (request["blocks"][0] + ".txt")
        chunk.write_bytes(chunk.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "hash/size"):
            transport.apply(self.root)

    def test_changed_base_fails(self):
        self.prepare()
        self.base.write_bytes(self.base.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "Base package hash"):
            transport.apply(self.root)

    def test_publish_and_idempotent_retry(self):
        latest = self.latest()
        self.prepare(latest)
        transport.apply(self.root)
        self.assertEqual((self.root / "updates/1.1.0.json").read_bytes(), self.target.read_bytes())
        self.assertEqual((self.root / "updates/latest.json").read_bytes(), latest.read_bytes())
        transport.apply(self.root)

    def test_stale_latest_fails_without_release_write(self):
        self.prepare(self.latest())
        (self.root / "updates/latest.json").write_text('{"version":"1.0.1"}')
        with self.assertRaisesRegex(ValueError, "changed since preparation"):
            transport.apply(self.root)
        self.assertFalse((self.root / "updates/1.1.0.json").exists())

    def test_existing_release_is_never_overwritten(self):
        self.prepare(self.latest())
        dest = self.root / "updates/1.1.0.json"
        dest.write_bytes(b"different")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            transport.apply(self.root)
        self.assertEqual(dest.read_bytes(), b"different")

    def test_unsafe_base_path_fails(self):
        request = self.prepare()
        request["base"] = "../outside.json"
        (self.root / "publishing/request.json").write_bytes(transport.pack(request))
        with self.assertRaisesRegex(ValueError, "base path"):
            transport.assemble(self.root)


if __name__ == "__main__":
    unittest.main()
