import tempfile
import unittest
from pathlib import Path

from app.config import ConfigError, Settings


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        s = Settings.load(env_file="/nao/existe.env", environ={})
        self.assertEqual(s.port, 8000)
        self.assertEqual(s.host, "127.0.0.1")
        self.assertEqual(s.db_path.name, "nym.db")

    def test_env_overrides_file(self):
        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text('# comentário\nNYM_MODEL="qwen2.5"\nNYM_PORT=9000\n', encoding="utf-8")
            s = Settings.load(env_file=env, environ={"NYM_PORT": "9100"})
        self.assertEqual(s.model, "qwen2.5")   # veio do arquivo
        self.assertEqual(s.port, 9100)         # ambiente vence o arquivo

    def test_invalid_values_raise(self):
        with self.assertRaises(ConfigError):
            Settings.load(env_file="/nao/existe.env", environ={"NYM_PORT": "abc"})
        with self.assertRaises(ConfigError):
            Settings.load(env_file="/nao/existe.env", environ={"NYM_CONTEXT_MESSAGES": "0"})

    def test_identity_settings(self):
        s = Settings.load(env_file="/nao/existe.env", environ={})
        self.assertEqual((s.identity_chars, s.identity_auto_apply), (3000, False))
        s = Settings.load(env_file="/nao/existe.env",
                          environ={"NYM_IDENTITY_AUTO_APPLY": "true", "NYM_IDENTITY_CHARS": "800"})
        self.assertEqual((s.identity_chars, s.identity_auto_apply), (800, True))
        with self.assertRaises(ConfigError):
            Settings.load(env_file="/nao/existe.env", environ={"NYM_IDENTITY_AUTO_APPLY": "talvez"})
        with self.assertRaises(ConfigError):
            Settings.load(env_file="/nao/existe.env", environ={"NYM_IDENTITY_CHARS": "100"})

    def test_sampling_settings(self):
        s = Settings.load(env_file="/nao/existe.env", environ={})
        self.assertEqual((s.temperature, s.num_predict, s.num_ctx), (0.8, 0, 8192))
        self.assertEqual(Settings.load(env_file="/nao/existe.env", environ={"NYM_NUM_CTX": "0"}).num_ctx, 0)
        s = Settings.load(env_file="/nao/existe.env", environ={"NYM_TEMPERATURE": "1.1", "NYM_NUM_PREDICT": "200"})
        self.assertEqual((s.temperature, s.num_predict), (1.1, 200))
        for bad in ({"NYM_TEMPERATURE": "3"}, {"NYM_TEMPERATURE": "-1"}, {"NYM_NUM_PREDICT": "-5"}):
            with self.assertRaises(ConfigError):
                Settings.load(env_file="/nao/existe.env", environ=bad)


if __name__ == "__main__":
    unittest.main()
