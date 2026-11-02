import importlib.util
import tempfile
import unittest
from pathlib import Path


def load_probe():
    path = Path(__file__).resolve().parent.parent / "scripts" / "latency_probe.py"
    spec = importlib.util.spec_from_file_location("latency_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load latency_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load_probe()


class ParseEventTests(unittest.TestCase):
    def test_responses_reasoning_delta(self):
        event = probe.parse_event(
            "responses",
            {
                "type": "response.reasoning_summary_text.delta",
                "delta": "thinking",
            },
        )
        self.assertEqual(event, {"kind": "reasoning", "text": "thinking"})

    def test_responses_text_delta(self):
        event = probe.parse_event(
            "responses",
            {"type": "response.output_text.delta", "delta": "pong"},
        )
        self.assertEqual(event, {"kind": "text", "text": "pong"})

    def test_responses_completed_carries_usage(self):
        event = probe.parse_event(
            "responses",
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                },
            },
        )
        self.assertEqual(event["kind"], "completed")
        self.assertEqual(event["usage"]["output_tokens"], 3)

    def test_chat_delta_and_usage(self):
        text = probe.parse_event(
            "chat",
            {"choices": [{"delta": {"content": "hi"}}]},
        )
        usage = probe.parse_event(
            "chat",
            {"choices": [], "usage": {"completion_tokens": 2}},
        )
        self.assertEqual(text, {"kind": "text", "text": "hi"})
        self.assertEqual(usage["kind"], "usage")
        self.assertEqual(usage["usage"]["completion_tokens"], 2)


class SummarizeTests(unittest.TestCase):
    def test_ttft_and_body_tps_use_visible_text_only(self):
        events = [
            (0.50, {"kind": "reasoning", "text": "think"}),
            (1.00, {"kind": "text", "text": "hello"}),
            (1.25, {"kind": "text", "text": " world"}),
            (
                1.50,
                {
                    "kind": "completed",
                    "status": "completed",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 13,
                        "input_tokens_details": {"cached_tokens": 80},
                        "output_tokens_details": {"reasoning_tokens": 3},
                    },
                },
            ),
        ]
        metrics = probe.summarize(events, 0.0, 1.50)

        self.assertAlmostEqual(metrics["first_reasoning_ms"], 500)
        self.assertAlmostEqual(metrics["ttft_ms"], 1000)
        self.assertAlmostEqual(metrics["total_ms"], 1500)
        self.assertAlmostEqual(metrics["body_tps"], 40)
        self.assertEqual(metrics["cached_tokens"], 80)
        self.assertEqual(metrics["reasoning_tokens"], 3)
        self.assertEqual(metrics["body_tokens"], 10)

    def test_usage_without_text_still_reports_status(self):
        metrics = probe.summarize(
            [
                (
                    0.1,
                    {
                        "kind": "completed",
                        "status": "incomplete",
                        "usage": {"output_tokens": 8},
                    },
                )
            ],
            0.0,
            0.1,
        )
        self.assertEqual(metrics["status"], "incomplete")
        self.assertIsNone(metrics["ttft_ms"])
        self.assertEqual(metrics["output_tokens"], 8)


class PercentileTests(unittest.TestCase):
    def test_p50_and_p95_ignore_none(self):
        values = [None, 100, 200, 300, 400, 500]
        self.assertEqual(probe._percentile(values, 0.5), 300)
        self.assertEqual(probe._percentile(values, 0.95), 500)
        self.assertIsNone(probe._percentile([None], 0.5))


class RequestBodyTests(unittest.TestCase):
    def test_responses_image_is_attached_to_user_content(self):
        body = probe._request_body(
            "responses",
            "model",
            "describe",
            128,
            "high",
            "warm",
            image_url="data:image/png;base64,AAAA",
        )
        content = body["input"][0]["content"]
        self.assertEqual(content[0], {"type": "input_text", "text": "describe"})
        self.assertEqual(
            content[1],
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
        )

    def test_chat_image_is_attached_to_user_content(self):
        body = probe._request_body(
            "chat",
            "model",
            "describe",
            128,
            None,
            "warm",
            image_url="data:image/png;base64,AAAA",
        )
        content = body["messages"][0]["content"]
        self.assertEqual(
            content[1],
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAAA"},
            },
        )

    def test_image_data_url_preserves_mime_type(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.png"
            path.write_bytes(b"png")
            value = probe._image_data_url(path)
        self.assertTrue(value.startswith("data:image/png;base64,"))


if __name__ == "__main__":
    unittest.main()
