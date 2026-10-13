"""Newline-framed TCP input for a single, sequential position controller."""

import json
import logging
import math
import socket
from threading import Event
import time

from servo_axis import AxisStatus, NormalizedAxis, integer_in_range, normalized_value


MAX_LINE_BYTES = 128
LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())


class MotionCommandError(RuntimeError):
    """A control failure terminates the server instead of accepting more targets."""


def positive_seconds(value: float) -> float:
    if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise ValueError("Network timeout must be positive and finite.")
    return value


class PositionTCPServer:
    """One client at a time; one completed movement per newline-delimited number.

    The caller owns the connected/armed axis and closes it when serving ends.
    All axis I/O runs on the thread calling serve_forever().
    """

    def __init__(self, axis: NormalizedAxis, host: str = "127.0.0.1", port: int = 5000,
                 *, client_timeout_s: float = 30.0):
        integer_in_range("port", port, 0, 65535)
        self.axis = axis
        self.host = host
        self.port = port
        self.client_timeout_s = positive_seconds(client_timeout_s)
        self._listener = None

    @property
    def address(self) -> tuple[str, int]:
        if self._listener is None:
            raise RuntimeError("TCP server is not listening.")
        return self._listener.getsockname()

    def __enter__(self) -> "PositionTCPServer":
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # Windows SO_REUSEADDR permits another socket to share the port.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind((self.host, self.port))
            listener.listen(1)
            listener.settimeout(0.2)
        except BaseException:
            listener.close()
            raise
        self._listener = listener
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None

    @staticmethod
    def _reply(connection: socket.socket, payload: dict) -> bool:
        try:
            message = json.dumps(payload, allow_nan=False, separators=(",", ":"))
            connection.sendall(message.encode("utf-8") + b"\n")
            return True
        except OSError:
            # The completed command must never be automatically retried.
            return False

    def _error(self, connection: socket.socket, code: str, message: str) -> bool:
        LOGGER.warning("TCP %s: %s", code, message)
        return self._reply(connection, {"ok": False, "error": code, "message": message})

    def _handle_line(self, connection: socket.socket, line: bytes) -> bool:
        try:
            value = normalized_value(line.decode("ascii"))
        except (UnicodeDecodeError, ValueError) as exc:
            return self._error(connection, "invalid_value", str(exc))
        target = self.axis.config.target_counts(value)
        LOGGER.info("MOVE request: input=%s target=%d", value, target)
        started = time.monotonic()
        last_report = started - 1.0

        def progress(state: AxisStatus) -> None:
            nonlocal last_report
            now = time.monotonic()
            if now - last_report >= 1.0:
                LOGGER.info("MOVING: elapsed=%.1fs feedback=%d remaining=%d",
                            now - started, state.position_counts, target - state.position_counts)
                last_report = now

        try:
            feedback = self.axis.move_normalized(value, on_progress=progress)
        except Exception as exc:
            self._error(connection, "motion_error", str(exc))
            raise MotionCommandError(f"TCP position command failed: {exc}") from exc
        LOGGER.info("MOVE done: input=%s target=%d feedback=%d elapsed=%.3fs",
                    value, target, feedback, time.monotonic() - started)
        return self._reply(connection, {
            "ok": True, "input": value, "target_counts": target,
            "feedback_counts": feedback,
        })

    def _serve_client(self, connection: socket.socket, stop_event: Event) -> None:
        connection.settimeout(min(0.2, self.client_timeout_s))
        pending = bytearray()
        deadline = time.monotonic() + self.client_timeout_s
        while not stop_event.is_set():
            if time.monotonic() >= deadline:
                self._error(connection, "client_timeout", "No complete input line before timeout.")
                return
            try:
                data = connection.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                if pending:
                    self._error(connection, "incomplete_line", "Input must end with a newline.")
                return
            pending.extend(data)
            while not stop_event.is_set():
                newline = pending.find(b"\n")
                length = len(pending) if newline < 0 else newline
                if length > MAX_LINE_BYTES:
                    self._error(connection, "line_too_long",
                                f"Input is limited to {MAX_LINE_BYTES} bytes per line.")
                    return
                if newline < 0:
                    break
                line = bytes(pending[:newline])
                del pending[:newline + 1]
                if not self._handle_line(connection, line):
                    return
                # An accepted point-to-point move has its own motion timeout.
                # A partial line cannot keep a session alive by dripping bytes.
                deadline = time.monotonic() + self.client_timeout_s

    def serve_forever(self, *, stop_event: Event | None = None, once: bool = False) -> None:
        if self._listener is None:
            raise RuntimeError("Use the TCP server as a context manager before serving.")
        stop_event = stop_event if stop_event is not None else Event()
        while not stop_event.is_set():
            try:
                connection, peer = self._listener.accept()
            except socket.timeout:
                continue
            LOGGER.info("TCP connected: %s:%d", *peer)
            try:
                with connection:
                    self._serve_client(connection, stop_event)
            finally:
                LOGGER.info("TCP disconnected: %s:%d", *peer)
            if once:
                return
