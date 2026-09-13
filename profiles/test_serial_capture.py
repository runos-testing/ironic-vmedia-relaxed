"""Keep PTY cleanup on the capture thread when a reader disconnects."""
import threading
import unittest
from unittest.mock import Mock, patch
from serial_capture import SerialCapture


class CaptureCloseTest(unittest.TestCase):
    def test_disconnect_signals_ssh_without_closing_its_pty_from_two_threads(self):
        capture = SerialCapture.__new__(SerialCapture)
        capture.stop = threading.Event()
        capture.client = Mock(pid=12345)
        capture.client.close.side_effect = RuntimeError('Concurrent PTY close')
        capture.thread = Mock()
        capture.thread.is_alive.return_value = False
        with patch('os.kill') as kill:
            capture.close()
        self.assertTrue(capture.stop.is_set())
        kill.assert_called_once()
        capture.client.close.assert_not_called()
        capture.thread.join.assert_called_once()
