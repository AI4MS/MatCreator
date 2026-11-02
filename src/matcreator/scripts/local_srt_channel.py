"""Bounded byte multiplexing over anonymous pipes; no host socket capability.

Only the host opens flows. Worker flows connect to its own network namespace's
fixed Web address. HTTP, SSE and WebSocket bytes have identical treatment.
"""
from __future__ import annotations

import queue
import socket
import struct
import threading


OPEN, DATA, FIN, CLOSE, WINDOW = 1, 2, 3, 4, 5
HEADER = struct.Struct("!BII")
MAX_FRAME = 65536
MAX_FLOWS = 64
INGRESS_KIND = "stdio-mux-v1"


class ChannelError(RuntimeError):
    pass


def read_frame(stream):
    def exact(length):
        result = bytearray()
        while len(result) < length:
            part = stream.read(length - len(result))
            if not part:
                raise ChannelError("Account ingress pipe ended")
            result.extend(part)
        return bytes(result)
    kind, flow_id, length = HEADER.unpack(exact(HEADER.size))
    if (kind not in (OPEN, DATA, FIN, CLOSE, WINDOW) or not flow_id or length > MAX_FRAME
            or (kind != DATA and length) or (kind == DATA and not length)):
        raise ChannelError("Invalid account ingress frame")
    return kind, flow_id, exact(length) if length else b""


class _Flow:
    def __init__(self, connection):
        self.connection = connection
        self.incoming = queue.Queue(maxsize=5)  # Four data credits plus EOF.
        self.credit = 4
        self.local_finished = False
        self.remote_finished = False
        self.remote_fin_received = False
        self.close_sent = False
        self.close_received = False


class MuxChannel:
    """A maximum of 64 flows with 256KiB per-flow and 4MiB shared queues.

    CLOSE is acknowledged before a flow is forgotten. This safely drains frames
    already in flight without accepting messages for an unknown flow id.
    """
    def __init__(self, reader, writer, *, worker=False, target=("127.0.0.1", 8001), max_flows=MAX_FLOWS):
        if not 1 <= max_flows <= MAX_FLOWS:
            raise ValueError("Invalid account ingress flow limit")
        if worker and target != ("127.0.0.1", 8001):
            raise ValueError("Sandbox ingress target is fixed")
        self.reader, self.writer = reader, writer
        self.worker, self.target, self.max_flows = worker, target, max_flows
        self.outgoing = queue.Queue(maxsize=64)
        self.flows = {}
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.error = None
        self.next_id = 1
        self.last_remote_id = 0
        self.threads = []

    def start(self):
        for name, action in (("reader", self._read), ("writer", self._write)):
            thread = threading.Thread(target=action, name="mc-srt-" + name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def fail(self, message):
        with self.lock:
            if not self.stop_event.is_set():
                self.error = str(message)
                self.stop_event.set()
            for flow in self.flows.values():
                self._close_socket(flow)

    @staticmethod
    def _close_socket(flow):
        if flow.connection is not None:
            try:
                flow.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            flow.connection.close()

    def close(self):
        self.fail("Account ingress stopped")

    def _send(self, kind, flow_id, payload=b""):
        if self.stop_event.is_set():
            raise ChannelError("Account ingress is closed")
        try:
            self.outgoing.put((kind, flow_id, payload), timeout=5)
        except queue.Full as exc:
            self.fail("Account ingress output queue is full")
            raise ChannelError("Account ingress output queue is full") from exc

    def open(self, connection):
        if self.worker:
            raise ChannelError("Sandbox cannot open host ingress flows")
        with self.lock:
            if self.stop_event.is_set() or len(self.flows) >= self.max_flows or self.next_id > 0xffffffff:
                connection.close()
                return False
            flow_id = self.next_id
            self.next_id += 1
            flow = _Flow(connection)
            self.flows[flow_id] = flow
            self._send(OPEN, flow_id)
            self._start_flow(flow_id, flow)
            return True

    def _start_flow(self, flow_id, flow):
        flow.connection.settimeout(0.5)
        # Finished threads are not retained; live threads are bounded by flows.
        self.threads = [thread for thread in self.threads if thread.is_alive()]
        for name, action in (("socket-read", self._socket_read), ("socket-write", self._socket_write)):
            thread = threading.Thread(target=action, args=(flow_id, flow), name="mc-srt-" + name, daemon=True)
            self.threads.append(thread)
            thread.start()

    def _finish(self, flow_id, flow):
        with self.lock:
            # Wait for every outgoing data credit to return. A peer's pipe
            # reader can otherwise see CLOSE before its socket writer drains
            # already queued payload, truncating large downloads.
            if flow.local_finished and flow.remote_finished and flow.credit == 4:
                self._terminate(flow_id, flow)

    def _terminate(self, flow_id, flow):
        with self.lock:
            self._close_socket(flow)
            if not flow.close_sent:
                flow.close_sent = True
                self._send(CLOSE, flow_id)
            if flow.close_received:
                self.flows.pop(flow_id, None)

    def _socket_read(self, flow_id, flow):
        try:
            while not self.stop_event.is_set() and not flow.close_sent:
                with self.lock:
                    if flow.credit <= 0:
                        reserved = False
                    else:
                        flow.credit -= 1
                        reserved = True
                if not reserved:
                    self.stop_event.wait(0.01)
                    continue
                try:
                    chunk = flow.connection.recv(MAX_FRAME)
                except socket.timeout:
                    with self.lock:
                        flow.credit += 1
                    continue
                with self.lock:
                    if flow.close_sent:
                        return
                    if not chunk:
                        flow.credit += 1
                        flow.local_finished = True
                        self._send(FIN, flow_id)
                        self._finish(flow_id, flow)
                        return
                    self._send(DATA, flow_id, chunk)
        except (OSError, ChannelError):
            if not self.stop_event.is_set():
                self._terminate(flow_id, flow)

    def _socket_write(self, flow_id, flow):
        try:
            while not self.stop_event.is_set() and not flow.close_sent:
                try:
                    chunk = flow.incoming.get(timeout=0.5)
                except queue.Empty:
                    continue
                if chunk is None:
                    try:
                        flow.connection.shutdown(socket.SHUT_WR)
                    except OSError:
                        pass
                    flow.remote_finished = True
                    self._finish(flow_id, flow)
                    return
                view = memoryview(chunk)
                while view and not self.stop_event.is_set() and not flow.close_sent:
                    try:
                        count = flow.connection.send(view)
                    except socket.timeout:
                        continue
                    if count <= 0:
                        raise OSError("Closed account flow")
                    view = view[count:]
                with self.lock:
                    if not flow.close_sent:
                        self._send(WINDOW, flow_id)
        except (OSError, ChannelError):
            if not self.stop_event.is_set():
                self._terminate(flow_id, flow)

    def _read(self):
        try:
            while not self.stop_event.is_set():
                kind, flow_id, payload = read_frame(self.reader)
                with self.lock:
                    if kind == OPEN:
                        if (not self.worker or flow_id <= self.last_remote_id
                                or len(self.flows) >= self.max_flows):
                            raise ChannelError("Invalid account ingress open")
                        self.last_remote_id = flow_id
                        flow = _Flow(None)
                        self.flows[flow_id] = flow
                        try:
                            flow.connection = socket.create_connection(self.target, timeout=1)
                        except OSError:
                            self._terminate(flow_id, flow)
                        else:
                            self._start_flow(flow_id, flow)
                        continue
                    flow = self.flows.get(flow_id)
                    if flow is None:
                        raise ChannelError("Account ingress referenced an unknown flow")
                    if kind == CLOSE:
                        if flow.close_received:
                            raise ChannelError("Duplicate account flow closure")
                        flow.close_received = True
                        self._terminate(flow_id, flow)
                    elif flow.close_sent:
                        # The peer's already queued bytes precede its CLOSE ack.
                        continue
                    elif kind == WINDOW:
                        if flow.credit >= 4:
                            raise ChannelError("Invalid account flow credit")
                        flow.credit += 1
                        self._finish(flow_id, flow)
                    elif flow.remote_fin_received:
                        raise ChannelError("Data followed account flow EOF")
                    else:
                        if kind == FIN:
                            flow.remote_fin_received = True
                        try:
                            flow.incoming.put_nowait(None if kind == FIN else payload)
                        except queue.Full as exc:
                            raise ChannelError("Account flow input queue is full") from exc
        except (ChannelError, OSError, ValueError) as exc:
            self.fail(exc)

    def _write(self):
        try:
            while not self.stop_event.is_set():
                try:
                    kind, flow_id, payload = self.outgoing.get(timeout=0.5)
                except queue.Empty:
                    continue
                view = memoryview(HEADER.pack(kind, flow_id, len(payload)) + payload)
                while view:
                    count = self.writer.write(view)
                    if count is None or count <= 0:
                        raise ChannelError("Account ingress pipe cannot write")
                    view = view[count:]
                self.writer.flush()
        except (ChannelError, OSError, ValueError) as exc:
            self.fail(exc)
