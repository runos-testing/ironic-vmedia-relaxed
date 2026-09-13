import unittest
from serial_pool import SerialPool


class Capture:
    def __init__(self, node, epoch):
        self.epoch = epoch
        self.expires = 0
        self.closed = False
        self.enabled = True
        self.state = 'connected'

    def allowed(self):
        return self.enabled

    def close(self):
        self.closed = True

    def snapshot(self):
        return {'state': 'connected', 'text': self.epoch}


class SerialPoolTests(unittest.TestCase):
    def setUp(self):
        self.created = []
        self.now = 0
        def create(node, epoch):
            capture = Capture(node, epoch)
            self.created.append(capture)
            return capture
        self.pool = SerialPool(create, lambda: self.now)

    def test_readers_share_one_upstream(self):
        self.pool.request('node', 'allocation', 'start')
        self.pool.request('node', 'allocation', 'start')
        self.assertEqual(len(self.created), 1)

    def test_new_allocation_cannot_read_old_output(self):
        self.pool.request('node', 'old', 'start')
        result = self.pool.request('node', 'new', 'read')
        self.assertEqual(result['text'], '')
        self.assertTrue(self.created[0].closed)

    def test_expired_reader_releases_connection(self):
        self.pool.request('node', 'allocation', 'start')
        self.now = 46
        self.pool.reap()
        self.assertTrue(self.created[0].closed)
        self.assertEqual(self.pool.request('node', 'allocation', 'read')['state'], 'stopped')

    def test_explicit_stop_releases_connection(self):
        self.pool.request('node', 'allocation', 'start')
        self.pool.request('node', 'allocation', 'stop')
        self.assertTrue(self.created[0].closed)

    def test_provider_disable_releases_connection(self):
        self.pool.request('node', 'allocation', 'start')
        self.created[0].enabled = False
        self.pool.reap()
        self.assertTrue(self.created[0].closed)


if __name__ == '__main__':
    unittest.main()
