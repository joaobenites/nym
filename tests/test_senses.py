"""Fase 8: sentidos — visão (imagem, câmera, tela), olhar imagens de pastas e assistir vídeos do YouTube."""
import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

from app.core.chat_service import ChatService, InvalidInputError
from app.database.connection import connect, transaction
from app.memory.consolidation import Consolidator
from app.models.base import LLMProvider, LLMResponse, ModelUnavailableError, ProviderStatus
from app.senses import video, vision
from app.senses.vision import SenseError, VisionService, decode_image
from app.tools import folders
from app.tools.base import ToolContext, ToolError
from app.tools.registry import ToolRegistry
from tests.dreamtools import rows
from tests.fakes import DbTestCase, ScriptedProvider, prompt_of
from tests.memtools import clock_at, extraction, seed_chat
from tests.test_research import FakeHttp

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PNG_B64 = base64.b64encode(PNG).decode()
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


class Eyes(LLMProvider):
    """Modelo falso com visão: descreve imagens e conversa; guarda o que recebeu."""
    name = "eyes"
    model = "eyes-1"

    def __init__(self, replies=(), sees=True, describe="Um gato laranja dormindo num teclado.", fail=None):
        self.replies = list(replies)
        self.sees = sees
        self.description = describe
        self.fail = fail
        self.calls = []

    def supports_vision(self):
        return self.sees

    def chat(self, messages, *, temperature=None, json_mode=False):
        self.calls.append(list(messages))
        if messages[-1].images:
            if self.fail:
                raise self.fail
            return LLMResponse(self.description, self.model)
        return LLMResponse(self.replies.pop(0) if self.replies else "ok", self.model)

    def check_availability(self):
        return ProviderStatus(True, "ok")


class DecodeTests(unittest.TestCase):
    def test_accepts_real_images_and_refuses_the_rest(self):
        b64, raw, mime = decode_image("data:image/png;base64," + PNG_B64)
        self.assertEqual((raw, mime, b64), (PNG, "image/png", PNG_B64))
        self.assertEqual(decode_image(base64.b64encode(JPG).decode())[2], "image/jpeg")
        for bad in ("", "isto não é base64 de imagem", base64.b64encode(b"<svg onload=x>").decode(),
                    base64.b64encode(b"MZ\x90\x00 executavel").decode()):
            with self.assertRaises(SenseError, msg=bad[:20]):
                decode_image(bad)
        with self.assertRaises(SenseError):
            decode_image(base64.b64encode(b"\xff\xd8\xff" + b"0" * (vision.MAX_IMAGE_BYTES + 1)).decode())


class ChatVisionTests(DbTestCase):
    def chat(self, provider):
        senses = VisionService(self.settings, provider, clock=clock_at(1))
        return ChatService(self.settings, provider, clock=clock_at(1), tz=timezone.utc, senses=senses)

    def test_camera_image_is_seen_recorded_and_stays_in_history(self):
        eyes = Eyes(replies=["Que gato folgado!", "Ele estava no teclado, sim."])
        chat = self.chat(eyes)
        res = chat.send_message("olha quem tá aqui", [{"kind": "camera", "data": PNG_B64}])
        look = eyes.calls[0]
        self.assertEqual(look[-1].images, (PNG_B64,))
        self.assertIn("câmera do computador", look[-1].content)
        self.assertIn("olha quem tá aqui", look[-1].content)                 # a fala dele ajuda a focar
        p = rows(self.settings, "SELECT * FROM perceptions")[0]
        self.assertEqual((p["kind"], p["message_id"], p["image_path"]), ("camera", res.user_message_id, None))
        reply_ctx = eyes.calls[1][-1].content
        self.assertIn("[O que você viu junto desta mensagem (câmera dele): Um gato laranja", reply_ctx)
        self.assertFalse(eyes.calls[1][-1].images)                           # a conversa usa a descrição registrada
        chat.send_message("lembra do que te mostrei?")
        self.assertIn("Um gato laranja", " ".join(m.content for m in eyes.calls[2]))
        hist = chat.get_history()["messages"]
        self.assertEqual(hist[0]["perceptions"][0]["kind"], "camera")

    def test_images_are_refused_before_anything_is_saved_when_he_cannot_see(self):
        chat = self.chat(Eyes(sees=False))
        with self.assertRaises(InvalidInputError):
            chat.send_message("olha", [{"kind": "camera", "data": PNG_B64}])
        with self.assertRaises(InvalidInputError):
            self.chat(Eyes()).send_message("olha", [{"kind": "camera", "data": "lixo"}])
        with self.assertRaises(InvalidInputError):
            self.chat(Eyes()).send_message("olha", [{"kind": "webcam-secreta", "data": PNG_B64}])
        with self.assertRaises(InvalidInputError):
            self.chat(Eyes()).send_message("olha", [{"data": PNG_B64}] * 4)
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM messages")[0]["n"], 0)

    def test_failed_vision_is_recorded_honestly(self):
        eyes = Eyes(replies=["Não consegui ver direito, manda de novo?"], fail=ModelUnavailableError("ollama caiu"))
        self.chat(eyes).send_message("e isso?", [{"kind": "screen", "data": PNG_B64}])
        self.assertIn("Não consegui enxergar", eyes.calls[1][-1].content)
        self.assertTrue(json.loads(rows(self.settings, "SELECT details FROM perceptions")[0]["details"])["failed"])

    def test_streaming_reports_seeing(self):
        eyes = Eyes(replies=["Bonita a tela!"])
        events = list(self.chat(eyes).stream_message("minha tela", [{"kind": "screen", "data": PNG_B64}]))
        kinds = [e["type"] for e in events]
        self.assertEqual(kinds[:3], ["seeing", "seen", "start"])
        self.assertEqual(events[1]["perceptions"][0]["kind"], "screen")
        self.assertEqual(kinds[-1], "done")

    def test_keep_images_saves_and_forget_deletes(self):
        settings = self.make_settings(keep_images=True)
        eyes = Eyes(replies=["oi"])
        ChatService(settings, eyes, senses=VisionService(settings, eyes)).send_message("foto", [{"data": PNG_B64}])
        p = rows(settings, "SELECT * FROM perceptions")[0]
        self.assertTrue(Path(p["image_path"]).is_file())
        conn = connect(settings.db_path)
        with transaction(conn):
            vision.delete(conn, p["id"])
        conn.close()
        self.assertFalse(Path(p["image_path"]).exists())
        self.assertEqual(rows(settings, "SELECT COUNT(*) AS n FROM perceptions")[0]["n"], 0)

    def test_memory_consolidation_sees_it_as_seen_not_said(self):
        ids = seed_chat(self.settings, [("user", "olha o meu gato novo que eu adotei semana passada, ele dorme o dia todo", 0),
                                        ("assistant", "Que lindo, ele parece muito tranquilo!", 1),
                                        ("user", "ele se chama Laranjinha e adora o teclado do computador", 2)])
        conn = connect(self.settings.db_path)
        with transaction(conn):
            vision.store(conn, "camera", "câmera", "Um gato laranja dormindo.", "2026-10-01T10:00:00.000+00:00",
                         message_id=ids[0])
        conn.close()
        provider = ScriptedProvider([extraction("Ele mostrou o gato.")])
        Consolidator(self.settings, provider, clock=clock_at(60 * 24)).run_once()
        self.assertIn("descrito pela visão do Nym, não dito pelo usuário: Um gato laranja", prompt_of(provider.calls[0]))


class VisionModelTests(unittest.TestCase):
    def test_separate_vision_model_is_used_when_configured(self):
        from app.config import Settings
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(data_dir=Path(tmp), vision_model="olhos:latest")
            made = []
            svc = VisionService(settings, Eyes(sees=False), make_provider=lambda m: made.append(m) or Eyes())
            self.assertTrue(svc.status()["available"])
            self.assertEqual(made, ["olhos:latest"])


VTT = """WEBVTT
Kind: captions
Language: pt

00:00:00.000 --> 00:00:02.000
<c>olá</c> pessoal

00:00:02.000 --> 00:00:04.000
olá pessoal
hoje vamos editar

00:00:04.000 --> 00:00:06.000
hoje vamos editar um vídeo no DaVinci
"""


class FakeBackend:
    available = True

    def __init__(self, info, make_video=None):
        self._info = info
        self._make = make_video
        self.downloads = 0

    def info(self, url):
        return self._info

    def download_light(self, url, folder):
        self.downloads += 1
        if self._make is None:
            raise ToolError("download indisponível no teste")
        return self._make(folder)


def tiny_video(folder):
    out = Path(folder) / "video.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=duration=6:size=320x240:rate=5", "-pix_fmt", "yuv420p", str(out)], check=True, timeout=60)
    return out


INFO = {"title": "Editando no DaVinci", "channel": "Canal Teste", "duration": 6, "language": "pt",
        "subtitles": {"live_chat": [{"ext": "json", "url": "https://x/chat"}]},
        "automatic_captions": {"pt-orig": [{"ext": "vtt", "url": "https://captions.example/pt-orig.vtt"}],
                               "en": [{"ext": "vtt", "url": "https://captions.example/en-traduzida.vtt"}]},
        "chapters": [{"start_time": 0, "title": "Abertura"}]}


class VideoParsingTests(unittest.TestCase):
    def test_youtube_links(self):
        for url in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtu.be/dQw4w9WgXcQ?t=3",
                    "https://m.youtube.com/watch?feature=x&v=dQw4w9WgXcQ", "https://www.youtube.com/shorts/dQw4w9WgXcQ"):
            self.assertEqual(video.youtube_id(url), "dQw4w9WgXcQ", url)
        for bad in ("https://vimeo.com/123", "https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ", "javascript:alert(1)"):
            self.assertIsNone(video.youtube_id(bad), bad)

    def test_caption_choice_avoids_translations(self):
        self.assertEqual(video.pick_caption(INFO)[2:], ("legenda automática (idioma original)", "pt"))
        manual = {**INFO, "subtitles": {"en": [{"ext": "vtt", "url": "u-en"}], "pt-BR": [{"ext": "json3", "url": "u-pt"}]}}
        self.assertEqual(video.pick_caption(manual), ("u-pt", "json3", "legenda do autor", "pt-BR"))
        self.assertIsNone(video.pick_caption({"automatic_captions": {"en": [{"ext": "vtt", "url": "x"}]}, "language": "ja"}))

    def test_caption_text_removes_timing_tags_and_repeats(self):
        self.assertEqual(video.caption_text(VTT, "vtt"), "olá pessoal hoje vamos editar um vídeo no DaVinci")
        j3 = json.dumps({"events": [{"segs": [{"utf8": "primeira "}, {"utf8": "frase"}]}, {"segs": [{"utf8": "\n"}]},
                                    {"segs": [{"utf8": "segunda"}]}]})
        self.assertEqual(video.caption_text(j3, "json3"), "primeira frase segunda")

    def test_frame_times_are_spread(self):
        self.assertEqual(video.frame_times(100, 4), [16.25, 38.75, 61.25, 83.75])
        self.assertEqual(video.frame_times(0, 4), [])


class WatchTests(DbTestCase):
    def ctx(self, eyes, http, backend, goal_id=None, **settings):
        s = self.make_settings(**settings) if settings else self.settings
        senses = VisionService(s, eyes)
        senses.video_backend = backend
        return ToolContext(settings=s, clock=clock_at(0), tz=timezone.utc, http=http, provider=eyes, senses=senses,
                           goal_id=goal_id)

    def http(self):
        h = FakeHttp({"https://www.youtube.com/oembed": ("u", "application/json",
                                                         json.dumps({"title": "Editando no DaVinci", "author_name": "Canal Teste"})),
                      "https://captions.example/pt-orig.vtt": ("u", "text/vtt", VTT)})
        h.images = []
        h.get_image = lambda url: h.images.append(url) or JPG
        return h

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg não instalado")
    def test_watch_hears_captions_sees_frames_and_remembers(self):
        eyes = Eyes(replies=["Sobre o vídeo: um tutorial de edição.\nO que vi e ouvi: abrir o DaVinci."], describe="Barras coloridas de teste.")
        http = self.http()
        backend = FakeBackend(INFO, tiny_video)
        tool = ToolRegistry().get("watch_video")
        out = tool.run(self.ctx(eyes, http, backend), tool.validate({"url": "https://youtu.be/dQw4w9WgXcQ"}))
        self.assertIn("ouvi pela legenda automática (idioma original)", out)
        self.assertIn("olhei 4 imagem(ns)", out)
        self.assertEqual(backend.downloads, 1)
        self.assertFalse(any("traduzida" in c[0] for c in http.calls))       # nunca pediu a legenda traduzida
        self.assertFalse(http.images)                                          # teve quadros: não precisou das miniaturas
        final = eyes.calls[-1][-1].content
        self.assertIn("hoje vamos editar um vídeo no DaVinci", final)
        self.assertIn("Barras coloridas", final)
        self.assertIn("Abertura", final)
        p = rows(self.settings, "SELECT * FROM perceptions")[0]
        self.assertEqual((p["kind"], p["source"], p["title"]), ("video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "Editando no DaVinci"))
        self.assertEqual(len(json.loads(p["details"])["frames"]), 4)
        mem = rows(self.settings, "SELECT kind, content FROM memories")[0]
        self.assertEqual(mem["kind"], "episodic")
        self.assertIn("Assisti ao vídeo \"Editando no DaVinci\" (Canal Teste)", mem["content"])
        finding = ToolRegistry().get("save_finding")
        ok = finding.run(self.ctx(eyes, http, backend), finding.validate(
            {"text": "O DaVinci Resolve tem versão gratuita", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}))
        self.assertIn("fonte externa", ok)
        self.assertFalse(list(Path(tempfile.gettempdir()).glob("nym-video-*")))   # o vídeo baixado foi apagado

    def test_without_ytdlp_it_uses_thumbnails_and_says_so(self):
        eyes = Eyes(replies=["Sobre o vídeo: vi só as miniaturas."])
        http = self.http()

        class NoBackend:
            available = False
        tool = ToolRegistry().get("watch_video")
        out = tool.run(self.ctx(eyes, http, NoBackend()), tool.validate({"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}))
        self.assertIn("não ouvi a fala", out)
        self.assertEqual(len(http.images), 4)
        self.assertIn("yt-dlp não instalado", eyes.calls[-1][-1].content)

    def test_refuses_other_sites_and_nothing_seen(self):
        tool = ToolRegistry().get("watch_video")
        with self.assertRaises(ToolError):
            tool.run(self.ctx(Eyes(), self.http(), FakeBackend(INFO)), tool.validate({"url": "https://vimeo.com/1"}))

        class NoBackend:
            available = False
        with self.assertRaises(ToolError):
            tool.run(self.ctx(Eyes(sees=False), self.http(), NoBackend()),
                     tool.validate({"url": "https://youtu.be/dQw4w9WgXcQ"}))
        self.assertEqual(rows(self.settings, "SELECT COUNT(*) AS n FROM perceptions")[0]["n"], 0)

    def test_watch_needs_permission_by_default(self):
        conn = connect(self.settings.db_path)
        self.addCleanup(conn.close)
        reg = ToolRegistry()
        self.assertEqual(reg.mode(conn, "watch_video"), "ask")
        self.assertEqual(reg.mode(conn, "look_at_image"), "auto")


class LookAtImageTests(DbTestCase):
    def test_only_images_inside_authorized_folders(self):
        pics = Path(self.tmp.name) / "fotos"
        pics.mkdir()
        (pics / "gato.png").write_bytes(PNG)
        (pics / "falso.png").write_bytes(b"MZ executavel")
        (pics / "nota.txt").write_text("oi")
        conn = connect(self.settings.db_path)
        with transaction(conn):
            folders.add_folder(conn, str(pics), "fotos")
        conn.close()
        eyes = Eyes()
        ctx = ToolContext(settings=self.settings, provider=eyes, senses=VisionService(self.settings, eyes), clock=clock_at(0))
        tool = ToolRegistry().get("look_at_image")
        self.assertIn("gato laranja", tool.run(ctx, tool.validate({"folder": "fotos", "path": "gato.png"})))
        for bad in ("falso.png", "nota.txt", "../fotos/../../segredo.png"):
            with self.assertRaises(ToolError, msg=bad):
                tool.run(ctx, tool.validate({"folder": "fotos", "path": bad}))
        self.assertEqual(rows(self.settings, "SELECT kind, source FROM perceptions")[0]["kind"], "file")


if __name__ == "__main__":
    unittest.main()


class OllamaVisionTests(unittest.TestCase):
    def test_payload_carries_images_and_capabilities_are_read(self):
        from unittest import mock

        from app.models.base import ChatMessage, ModelError
        from app.models.ollama_provider import OllamaProvider
        p = OllamaProvider("http://127.0.0.1:11434", "gemma4")
        payload = p._payload([ChatMessage("system", "s"), ChatMessage("user", "olha", images=("AAA",))], None, False, False)
        self.assertNotIn("images", payload["messages"][0])
        self.assertEqual(payload["messages"][1]["images"], ["AAA"])
        with mock.patch.object(p, "_request", return_value={"capabilities": ["completion", "vision"]}) as req:
            self.assertTrue(p.supports_vision())
            self.assertTrue(p.supports_vision())
            self.assertEqual(req.call_count, 1)                     # guardado depois da primeira resposta
        q = OllamaProvider("http://127.0.0.1:11434", "texto-so")
        with mock.patch.object(q, "_request", side_effect=ModelError("fora")):
            self.assertFalse(q.supports_vision())
        with mock.patch.object(q, "_request", return_value={"capabilities": ["completion"]}):
            self.assertFalse(q.supports_vision())


class ProtectionTests(unittest.TestCase):
    def test_nym_cannot_propose_changes_to_his_senses(self):
        from app.creations import selfcode
        for rel in ("app/senses/vision.py", "frontend/senses.js", "app/tools/senses_tools.py"):
            self.assertTrue(selfcode.is_protected(rel), rel)
