"""Keep one leased capture per machine and isolate allocation buffers."""
import threading
import time


class SerialPool:
    def __init__(self, factory, clock=time.monotonic):
        self.factory = factory
        self.clock = clock
        self.sessions = {}
        self.lock = threading.Lock()

    def request(self, node, epoch, action):
        with self.lock:
            session = self.sessions.get(node)
            if session and session.epoch != epoch:
                session.close()
                del self.sessions[node]
                session = None
            if session and (not session.allowed() or (action == 'start' and session.state in ('error', 'stopped'))):
                session.close()
                del self.sessions[node]
                session = None
            if action == 'stop':
                if session:
                    session.close()
                    del self.sessions[node]
                return {'state': 'stopped', 'text': ''}
            if not session and action == 'start':
                session = self.factory(node, epoch)
                self.sessions[node] = session
            if not session:
                return {'state': 'stopped', 'text': ''}
            session.expires = self.clock() + 45
            return session.snapshot()

    def reap(self):
        with self.lock:
            for node, session in list(self.sessions.items()):
                if session.expires < self.clock() or not session.allowed():
                    session.close()
                    del self.sessions[node]
