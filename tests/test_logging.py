import logging
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.logging_setup import setup_logging, shutdown_logging


def file_handlers(logger):
    return [h for h in logger.handlers if isinstance(h, logging.FileHandler)]


class LoggingTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(shutdown_logging)

    def test_reconfigures_when_log_dir_changes_and_closes_old_file(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            logger = setup_logging(Settings(data_dir=Path(a)))
            old = file_handlers(logger)[0]
            self.assertIsNotNone(old.stream)
            logger = setup_logging(Settings(data_dir=Path(b)))
            self.assertIsNone(old.stream)  # arquivo antigo fechado
            self.assertEqual(len(file_handlers(logger)), 1)
            self.assertTrue(file_handlers(logger)[0].baseFilename.startswith(str(Path(b))))
            shutdown_logging()
            self.assertEqual(logger.handlers, [])

    def test_same_dir_is_not_duplicated(self):
        with tempfile.TemporaryDirectory() as a:
            s = Settings(data_dir=Path(a))
            setup_logging(s)
            logger = setup_logging(s)
            self.assertEqual(len(file_handlers(logger)), 1)
            shutdown_logging()


if __name__ == "__main__":
    unittest.main()
