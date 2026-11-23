"""Deed-reader checks that do not need QGIS or a live model."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from cogo_workbench.ai_client import AIError, Client, XAI_MODEL, pick
from cogo_workbench.deed_ai import check_call, find_description, quote_in_text
from cogo_workbench.engine import traverse


DEED = """WARRANTY DEED. Commence at the Northwest corner of Section 12; thence S 00°12'30" E
along the West line of said Section, 400.00 feet to the Point of Beginning; thence N 89°47'30" E,
200.00 feet; thence S 00°12'30" E, 100.00 feet; thence S 89°47'30" W, 200.00 feet; thence
N 00°12'30" W, 100.00 feet to the Point of Beginning. Containing 0.46 acres, more or less.
Subject to easements of record."""


class DeedAiTests(unittest.TestCase):
    def test_find_description_stops_at_the_acreage(self):
        text = find_description(DEED)
        self.assertTrue(text.startswith("Commence at the Northwest corner"))
        self.assertTrue(text.endswith("Containing 0.46 acres, more or less."))
        self.assertNotIn("easements", text)
        self.assertEqual(find_description("no description here"), "")

    def test_check_call_keeps_quoted_numbers_only(self):
        call = {"quote": "thence N 89°47'30\" E, 200.00 feet", "kind": "line",
                "bearing": "N 89°47'30\" E", "distance": 200.0}
        course = check_call(call, DEED)
        self.assertEqual(course.kind, "Line")
        self.assertEqual(course.distance, "200")
        segment = traverse((0, 0), [course])[0]
        self.assertAlmostEqual(segment.distance, 200)
        with self.assertRaisesRegex(ValueError, "quote"):
            check_call({"quote": "thence N 10 E, 50 feet", "kind": "line", "bearing": "N 10 E",
                        "distance": 50}, DEED)
        with self.assertRaisesRegex(ValueError, "distance"):
            check_call(dict(call, distance=300), DEED)
        with self.assertRaisesRegex(ValueError, "bearing number"):
            check_call(dict(call, bearing="N 89°47'31\" E"), DEED)

    def test_check_call_curve_and_running_call(self):
        text = ("thence along a curve to the right having a radius of 100.00 feet, an arc length of "
                "157.08 feet, chord bearing N 45°00'00\" E")
        course = check_call({"quote": text, "kind": "curve", "bearing": "N 45°00'00\" E", "radius": 100,
                             "arc_length": 157.08, "side": "right", "bearing_is": "chord"}, text)
        self.assertEqual((course.kind, course.side, course.basis, course.measure), ("Curve", "R", "Chord", "Arc"))
        running = "thence run West along the South line a distance of 208 feet"
        course = check_call({"quote": running, "kind": "line", "bearing": "West", "distance": 208}, running)
        self.assertEqual(course.direction, "W")
        self.assertEqual(course.distance, "208")
        self.assertTrue(quote_in_text(running, "  " + running.upper()))

    def test_clients_against_a_local_fake(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                if self.path.startswith("/api/tags"):
                    body = {"models": [{"name": "deepseek-ocr:3b"}, {"name": "qwen2.5vl:7b"}]}
                else:
                    body = {"data": [{"id": XAI_MODEL}]}
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.startswith("/api/chat"):
                    content = json.dumps({"calls": []}) if req.get("format") else "OCR TEXT"
                    body = {"message": {"content": content}}
                else:
                    content = json.dumps({"calls": []}) if any(
                        item.get("role") == "system" for item in req.get("messages", [])) else "PAGE"
                    body = {"choices": [{"message": {"content": content}}]}
                raw = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d" % server.server_port
        try:
            local = Client("ollama", url)
            self.assertEqual(pick(local.list_models(), "deepseek-ocr"), "deepseek-ocr:3b")
            self.assertEqual(local.chat("deepseek-ocr:3b", "Free OCR.", ["aGk="]), "OCR TEXT")
            self.assertEqual(local.chat_json("qwen2.5vl:7b", "x", {"type": "object"}), {"calls": []})
            hosted = Client("xai", url, "test-key")
            self.assertEqual(hosted.list_models(), [XAI_MODEL])
            self.assertEqual(hosted.chat(XAI_MODEL, "read", ["aGk="]), "PAGE")
            self.assertEqual(hosted.chat_json(XAI_MODEL, "calls", {"type": "object"}), {"calls": []})
        finally:
            server.shutdown()
            server.server_close()
        with self.assertRaises(AIError):
            Client("ollama", "http://example.com")
        with self.assertRaises(AIError):
            Client("xai", "http://example.com", "test-key")


if __name__ == "__main__":
    unittest.main()
