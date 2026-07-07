"""StackOverflow client — question search, top-answer lookup, error-signature extraction,
HTML-to-text conversion, debug() composition. No live network calls."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp"))
from stackoverflow import StackOverflowClient, _detect_language, _extract_error_signature, _strip_html  # noqa: E402

SEARCH_RESPONSE = {
    "items": [
        {"title": "TypeError: &#39;NoneType&#39; object is not subscriptable", "link": "https://stackoverflow.com/q/1",
         "score": 42, "answer_count": 3, "tags": ["python"], "question_id": 1, "is_answered": True},
        {"title": "Unanswered similar question", "link": "https://stackoverflow.com/q/2",
         "score": 0, "answer_count": 0, "tags": ["python"], "question_id": 2, "is_answered": False},
    ]
}

ANSWER_RESPONSE = {"items": [
    {"answer_id": 99, "score": 100, "is_accepted": True,
     "body": "<p>Check for <code>None</code> before indexing:</p><pre><code>if x is not None:\n    x[0]</code></pre>"},
]}

ERROR_RESPONSE = {"error_message": "invalid key", "items": []}


class TestHelpers(unittest.TestCase):
    def test_strip_html_converts_code_blocks_and_tags(self):
        out = _strip_html(ANSWER_RESPONSE["items"][0]["body"])
        self.assertIn("Check for `None` before indexing:", out)
        self.assertIn("```\nif x is not None:\n    x[0]\n```", out)
        self.assertNotIn("<p>", out)
        self.assertNotIn("<code>", out)

    def test_extract_error_signature_finds_last_exception_line(self):
        tb = 'Traceback (most recent call last):\n  File "x.py", line 1, in <module>\nTypeError: bad thing happened'
        self.assertEqual(_extract_error_signature(tb), "TypeError: bad thing happened")

    def test_extract_error_signature_falls_back_to_first_line(self):
        self.assertEqual(_extract_error_signature("just a plain message, no exception format"),
                          "just a plain message, no exception format")

    def test_detect_language_python_traceback(self):
        tb = 'Traceback (most recent call last):\n  File "app.py", line 1\nTypeError: bad'
        self.assertEqual(_detect_language(tb), "python")

    def test_detect_language_java_stack_trace(self):
        tb = "Exception in thread \"main\" java.lang.NullPointerException\n\tat App.main(App.java:12)"
        self.assertEqual(_detect_language(tb), "java")

    def test_detect_language_rust_panic(self):
        tb = "thread 'main' panicked at src/main.rs:4:5"
        self.assertEqual(_detect_language(tb), "rust")

    def test_detect_language_unknown_returns_none(self):
        self.assertIsNone(_detect_language("something broke, no idea why"))


class TestStackOverflowClient(unittest.TestCase):
    def setUp(self):
        self.client = StackOverflowClient(get_secret=lambda name, scope="global": None)

    def test_search_questions_parses_and_unescapes_titles(self):
        with patch.object(StackOverflowClient, "_get", return_value=SEARCH_RESPONSE):
            results = self.client.search_questions("typeerror nonetype", k=5)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["title"], "TypeError: 'NoneType' object is not subscriptable")
        self.assertEqual(results[0]["question_id"], 1)

    def test_search_questions_respects_k(self):
        with patch.object(StackOverflowClient, "_get", return_value=SEARCH_RESPONSE):
            results = self.client.search_questions("test", k=1)
        self.assertEqual(len(results), 1)

    def test_top_answer_parses_body_and_metadata(self):
        with patch.object(StackOverflowClient, "_get", return_value=ANSWER_RESPONSE):
            answer = self.client.top_answer(1)
        self.assertTrue(answer["is_accepted"])
        self.assertEqual(answer["score"], 100)
        self.assertEqual(answer["link"], "https://stackoverflow.com/a/99")
        self.assertIn("```", answer["body"])

    def test_top_answer_returns_none_when_no_answers(self):
        with patch.object(StackOverflowClient, "_get", return_value={"items": []}):
            self.assertIsNone(self.client.top_answer(1))

    def test_get_raises_on_api_error_message(self):
        with patch("stackoverflow.urllib.request.urlopen") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = (
                b'{"error_message": "invalid key", "items": []}'
            )
            with self.assertRaises(RuntimeError):
                self.client.search_questions("test")

    def test_debug_fetches_top_answer_only_for_answered_questions(self):
        def fake_get(path, params):
            if path == "search/advanced":
                return SEARCH_RESPONSE
            if path == "questions/1/answers":
                return ANSWER_RESPONSE
            raise AssertionError(f"unexpected path {path}")  # question 2 is unanswered — must not be called

        with patch.object(StackOverflowClient, "_get", side_effect=fake_get):
            results = self.client.debug("TypeError: 'NoneType' object is not subscriptable", k=5)
        self.assertEqual(len(results), 2)
        self.assertIsNotNone(results[0]["top_answer"])
        self.assertIsNone(results[1]["top_answer"])  # unanswered question → no answer fetched

    def test_debug_auto_tags_by_detected_language(self):
        captured = {}

        def fake_get(path, params):
            if path == "search/advanced":
                captured["params"] = params
                return SEARCH_RESPONSE
            return ANSWER_RESPONSE

        tb = 'Traceback (most recent call last):\n  File "app.py", line 1\nTypeError: bad'
        with patch.object(StackOverflowClient, "_get", side_effect=fake_get):
            self.client.debug(tb, k=1)
        self.assertEqual(captured["params"]["tagged"], "python")

    def test_debug_language_override_takes_precedence(self):
        captured = {}

        def fake_get(path, params):
            if path == "search/advanced":
                captured["params"] = params
                return SEARCH_RESPONSE
            return ANSWER_RESPONSE

        tb = 'Traceback (most recent call last):\n  File "app.py", line 1\nTypeError: bad'
        with patch.object(StackOverflowClient, "_get", side_effect=fake_get):
            self.client.debug(tb, language="django", k=1)
        self.assertEqual(captured["params"]["tagged"], "django")


class TestApiKeyInjection(unittest.TestCase):
    def test_api_key_added_to_request_params_when_configured(self):
        client = StackOverflowClient(get_secret=lambda name, scope="global": "the-key" if name == "STACKOVERFLOW_API_KEY" else None)
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"items": []}'

        def fake_urlopen(req, timeout=10):
            captured["url"] = req.full_url
            return FakeResponse()

        with patch("stackoverflow.urllib.request.urlopen", side_effect=fake_urlopen):
            client.search_questions("test")
        self.assertIn("key=the-key", captured["url"])

    def test_no_key_param_when_not_configured(self):
        client = StackOverflowClient(get_secret=lambda name, scope="global": None)
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"items": []}'

        def fake_urlopen(req, timeout=10):
            captured["url"] = req.full_url
            return FakeResponse()

        with patch("stackoverflow.urllib.request.urlopen", side_effect=fake_urlopen):
            client.search_questions("test")
        self.assertNotIn("key=", captured["url"])


if __name__ == "__main__":
    unittest.main()
