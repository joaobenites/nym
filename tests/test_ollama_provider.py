import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.models.base import ChatMessage, ModelError, ModelUnavailableError
from app.models.ollama_provider import OllamaProvider, _ThinkFilter, strip_thinking


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        routes = {}
        received = []

        def _respond(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            type(self).received.append((self.command, self.path, body))
            status, payload = type(self).routes.get(
                (self.command, self.path), (404, b'{"error":"rota inexistente"}')
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_GET = _respond
        do_POST = _respond

        def log_message(self, *args):
            pass

    return Handler


class OllamaProviderTests(unittest.TestCase):
    def setUp(self):
        self.handler = make_handler()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self.handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.provider = OllamaProvider(self.url, "meu-modelo", timeout=5)

    def route(self, method, path, status, body):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.handler.routes[(method, path)] = (status, raw)

    def test_chat_success_sends_expected_payload(self):
        self.route("POST", "/api/chat", 200, {"message": {"role": "assistant", "content": "olá!"}})
        resp = self.provider.chat([ChatMessage("system", "s"), ChatMessage("user", "oi")])
        self.assertEqual((resp.content, resp.model), ("olá!", "meu-modelo"))
        _, _, raw = self.handler.received[-1]
        sent = json.loads(raw)
        self.assertEqual(sent["model"], "meu-modelo")
        self.assertIs(sent["stream"], False)
        self.assertEqual(sent["messages"][1], {"role": "user", "content": "oi"})

    def test_sampling_options_are_sent_only_when_configured(self):
        self.route("POST", "/api/chat", 200, {"message": {"content": "ok"}})
        self.provider.chat([ChatMessage("user", "oi")])
        self.assertNotIn("options", json.loads(self.handler.received[-1][2]))
        tuned = OllamaProvider(self.url, "meu-modelo", timeout=5, temperature=0.7, num_predict=120)
        tuned.chat([ChatMessage("user", "oi")])
        self.assertEqual(json.loads(self.handler.received[-1][2])["options"],
                         {"temperature": 0.7, "num_predict": 120})
        windowed = OllamaProvider(self.url, "meu-modelo", timeout=5, num_ctx=8192)
        windowed.chat([ChatMessage("user", "oi")])
        self.assertEqual(json.loads(self.handler.received[-1][2])["options"], {"num_ctx": 8192})
        unlimited = OllamaProvider(self.url, "meu-modelo", timeout=5, temperature=0.5, num_predict=0)
        unlimited.chat([ChatMessage("user", "oi")])
        self.assertEqual(json.loads(self.handler.received[-1][2])["options"], {"temperature": 0.5})

    def test_missing_model_is_unavailable(self):
        self.route("POST", "/api/chat", 404, {"error": "model 'meu-modelo' not found"})
        with self.assertRaises(ModelUnavailableError) as ctx:
            self.provider.chat([ChatMessage("user", "oi")])
        self.assertIn("ollama pull", str(ctx.exception))

    def test_server_error_is_model_error_not_unavailable(self):
        self.route("POST", "/api/chat", 500, {"error": "boom"})
        with self.assertRaises(ModelError) as ctx:
            self.provider.chat([ChatMessage("user", "oi")])
        self.assertNotIsInstance(ctx.exception, ModelUnavailableError)

    def test_malformed_and_empty_responses(self):
        self.route("POST", "/api/chat", 200, b"isto nao e json")
        with self.assertRaises(ModelError):
            self.provider.chat([ChatMessage("user", "oi")])
        self.route("POST", "/api/chat", 200, {"message": {"content": "   "}})
        with self.assertRaises(ModelError):
            self.provider.chat([ChatMessage("user", "oi")])

    def test_connection_refused_is_unavailable(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        dead = OllamaProvider(f"http://127.0.0.1:{port}", "m", timeout=2)
        with self.assertRaises(ModelUnavailableError):
            dead.chat([ChatMessage("user", "oi")])
        status = dead.check_availability()  # nunca levanta
        self.assertFalse(status.available)

    def test_check_availability(self):
        self.route("GET", "/api/tags", 200, {"models": [{"name": "meu-modelo:latest"}]})
        self.assertTrue(self.provider.check_availability().available)
        self.route("GET", "/api/tags", 200, {"models": [{"name": "outro:7b"}]})
        status = self.provider.check_availability()
        self.assertFalse(status.available)
        self.assertIn("ollama pull", status.detail)

    def test_rejects_invalid_url_and_model(self):
        with self.assertRaises(ValueError):
            OllamaProvider("file:///etc/passwd", "m")
        with self.assertRaises(ValueError):
            OllamaProvider("http://127.0.0.1:11434", "  ")


if __name__ == "__main__":
    unittest.main()


class StreamingAndSpeedTests(unittest.TestCase):
    """Streaming, modelo mantido carregado, modo de pensar e medições de tempo."""
    setUp = OllamaProviderTests.setUp
    route = OllamaProviderTests.route

    def ndjson(self, *chunks):
        lines = [{"message": {"role": "assistant", "content": c}, "done": False} for c in chunks]
        lines.append({"message": {"role": "assistant", "content": ""}, "done": True, "eval_count": 20,
                      "eval_duration": 500_000_000, "prompt_eval_count": 300, "prompt_eval_duration": 100_000_000,
                      "load_duration": 0, "total_duration": 700_000_000})
        return "\n".join(json.dumps(x) for x in lines).encode()

    def test_stream_yields_pieces_and_stats(self):
        self.route("POST", "/api/chat", 200, self.ndjson("Oi", ", tudo", " bem?"))
        events = list(self.provider.stream_chat([ChatMessage("user", "oi")]))
        self.assertEqual("".join(e.text for e in events), "Oi, tudo bem?")
        self.assertTrue(events[-1].done)
        self.assertEqual(events[-1].stats["tokens_per_s"], 40.0)
        self.assertIs(json.loads(self.handler.received[-1][2])["stream"], True)

    def test_stream_errors(self):
        self.route("POST", "/api/chat", 200, b'{"error": "falhou no meio"}\n')
        with self.assertRaises(ModelError):
            list(self.provider.stream_chat([ChatMessage("user", "oi")]))
        self.route("POST", "/api/chat", 200, json.dumps({"message": {"content": "pela metade"}, "done": False}).encode())
        with self.assertRaises(ModelError):                      # conexão acabou sem "done"
            list(self.provider.stream_chat([ChatMessage("user", "oi")]))

    def test_keep_alive_and_think_are_sent(self):
        p = OllamaProvider(self.url, "meu-modelo", timeout=5, keep_alive="30m", think=False)
        self.route("POST", "/api/chat", 200, {"message": {"content": "ok"}, "eval_count": 2, "eval_duration": 100_000_000})
        resp = p.chat([ChatMessage("user", "oi")])
        sent = json.loads(self.handler.received[-1][2])
        self.assertEqual((sent["keep_alive"], sent["think"]), ("30m", False))
        self.assertEqual(resp.stats["eval_count"], 2)

    def test_think_is_dropped_when_the_model_refuses_it(self):
        p = OllamaProvider(self.url, "meu-modelo", timeout=5, think=False)
        calls = []
        original = p._request

        def flaky(method, path, payload=None, timeout=None):
            calls.append(payload)
            if payload and "think" in payload:
                raise ModelError('Ollama respondeu HTTP 400: "meu-modelo" does not support thinking')
            return original(method, path, payload, timeout)
        p._request = flaky
        self.route("POST", "/api/chat", 200, {"message": {"content": "ok"}})
        self.assertEqual(p.chat([ChatMessage("user", "oi")]).content, "ok")
        self.assertIsNone(p.think)
        self.assertNotIn("think", calls[-1])


class ThinkingLeakTests(unittest.TestCase):
    """Modelos com modo de pensar (ex.: Qwen) às vezes soltam <think>...</think> no texto: não é fala do Nym."""
    setUp = OllamaProviderTests.setUp
    route = OllamaProviderTests.route
    ndjson = StreamingAndSpeedTests.ndjson

    def test_chat_drops_leading_think_block(self):
        self.route("POST", "/api/chat", 200, {"message": {"content": "<think>\nhmm, JSON...\n</think>\n{\"goals\": []}"}})
        self.assertEqual(self.provider.chat([ChatMessage("user", "oi")]).content, '{"goals": []}')

    def test_only_thinking_is_an_empty_answer(self):
        self.route("POST", "/api/chat", 200, {"message": {"content": "<think>pensando sem fim"}})
        with self.assertRaises(ModelError):
            self.provider.chat([ChatMessage("user", "oi")])

    def test_stream_hides_think_split_across_pieces(self):
        self.route("POST", "/api/chat", 200, self.ndjson("<th", "ink>raciocínio", " longo</thi", "nk>\n\nOi", ", tudo bem?"))
        text = "".join(e.text for e in self.provider.stream_chat([ChatMessage("user", "oi")]))
        self.assertEqual(text, "Oi, tudo bem?")

    def test_normal_text_is_untouched(self):
        self.assertEqual(strip_thinking("a <think> no meio fica"), "a <think> no meio fica")
        f = _ThinkFilter()
        self.assertEqual(f.feed("<") + f.feed("b>oi</b>") + f.flush(), "<b>oi</b>")
        f = _ThinkFilter()
        self.assertEqual(f.feed("<t") + f.flush(), "<t")    # resposta curtinha que só parecia o começo
