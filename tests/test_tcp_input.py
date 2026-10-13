from contextlib import contextmanager
from dataclasses import replace
import json
import queue
import re
import socket
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread
import unittest

from dummy_tcp_client import exchange
from servo_axis import AxisConfig, NormalizedAxis, SimulatedBackend
from tcp_input import MAX_LINE_BYTES, MotionCommandError, PositionTCPServer


class RecordingBackend(SimulatedBackend):
    def __init__(self, initial_position=0):
        super().__init__(initial_position)
        self.targets = []
        self.stops = 0

    def start_absolute(self, target, config):
        self.targets.append(target)
        super().start_absolute(target, config)

    def stop(self):
        self.stops += 1


@contextmanager
def running_server(backend=None, *, client_timeout=2.0, expected_error=None, config=None):
    config = config or AxisConfig(0, 10000, 1000, position_tolerance_counts=0)
    backend = backend or RecordingBackend(config.min_position_counts)
    axis = NormalizedAxis(backend, config)
    stop = Event()
    ready = Event()
    errors = []
    address = []

    def run():
        try:
            with axis:
                with PositionTCPServer(axis, port=0, client_timeout_s=client_timeout) as server:
                    axis.arm()
                    address.append(server.address)
                    ready.set()
                    server.serve_forever(stop_event=stop)
        except BaseException as exc:
            errors.append(exc)
        finally:
            ready.set()

    worker = Thread(target=run, daemon=True)
    worker.start()
    try:
        if not ready.wait(3) or not address:
            raise AssertionError(f"Server did not start: {errors}")
        yield address[0], backend
    finally:
        stop.set()
        worker.join(timeout=4)
        if worker.is_alive():
            raise AssertionError("Server did not stop.")
        if expected_error is None and errors:
            raise AssertionError(f"Unexpected server error: {errors}") from errors[0]
        if expected_error is not None and (
                len(errors) != 1 or not isinstance(errors[0], expected_error)):
            raise AssertionError(f"Expected {expected_error}, got {errors}")


def read_reply(reader):
    line = reader.readline(4097)
    if not line.endswith(b"\n"):
        raise AssertionError(f"Missing complete response: {line!r}")
    return json.loads(line)


class TcpSocketTests(unittest.TestCase):
    def test_real_socket_values_use_configured_absolute_endpoints(self):
        config = AxisConfig(-2000, 6000, 1000, position_tolerance_counts=0)
        with running_server(config=config) as (address, backend):
            with socket.create_connection(address, timeout=2) as connection:
                with connection.makefile("rb") as reader:
                    for value, target in (("0", -2000), ("0.25", 0), ("0.5", 2000),
                                          ("1", 6000), ("0.5", 2000), ("0.5", 2000)):
                        reply = exchange(connection, reader, value)
                        self.assertEqual(reply, {"ok": True, "input": float(value),
                                                 "target_counts": target, "feedback_counts": target})
            self.assertEqual(backend.targets, [0, 2000, 6000, 2000])
        self.assertFalse(backend.ready)
        self.assertFalse(backend.opened)

    def test_invalid_lines_do_not_move_and_connection_remains_usable(self):
        with running_server() as (address, backend):
            with socket.create_connection(address, timeout=2) as connection:
                with connection.makefile("rb") as reader:
                    for value in (b"-0.1", b"1.01", b"NaN", b"inf", b"-inf", b"abc",
                                  b"", b"0.5 0.7", b"\x00", b"\xff"):
                        connection.sendall(value + b"\n")
                        reply = read_reply(reader)
                        self.assertFalse(reply["ok"])
                        self.assertEqual(reply["error"], "invalid_value")
                        self.assertEqual(backend.targets, [])
                    self.assertEqual(exchange(connection, reader, "0.5")["feedback_counts"], 5000)

    def test_fragmented_number_is_not_executed_until_newline(self):
        with running_server() as (address, backend):
            with socket.create_connection(address, timeout=2) as connection:
                connection.sendall(b"0.")
                connection.settimeout(0.1)
                with self.assertRaises(socket.timeout):
                    connection.recv(1)
                self.assertEqual(backend.targets, [])
                connection.settimeout(2)
                connection.sendall(b"5\r\n")
                with connection.makefile("rb") as reader:
                    self.assertEqual(read_reply(reader)["feedback_counts"], 5000)

    def test_coalesced_lines_are_processed_in_order(self):
        with running_server() as (address, backend):
            with socket.create_connection(address, timeout=2) as connection:
                connection.sendall(b"0.25\n0.5\n1\n")
                with connection.makefile("rb") as reader:
                    self.assertEqual([read_reply(reader)["feedback_counts"] for _ in range(3)],
                                     [2500, 5000, 10000])
            self.assertEqual(backend.targets, [2500, 5000, 10000])

    def test_oversized_line_closes_session_and_discards_following_command(self):
        for suffix in (b"", b"\n0.5\n"):
            with self.subTest(suffix=suffix), running_server() as (address, backend):
                with socket.create_connection(address, timeout=2) as connection:
                    connection.sendall(b"0" * (MAX_LINE_BYTES + 1) + suffix)
                    with connection.makefile("rb") as reader:
                        self.assertEqual(read_reply(reader)["error"], "line_too_long")
                        self.assertEqual(reader.readline(), b"")
                self.assertEqual(backend.targets, [])

    def test_incomplete_line_at_eof_is_discarded_and_new_client_can_connect(self):
        with running_server() as (address, backend):
            with socket.create_connection(address, timeout=2) as connection:
                connection.sendall(b"0.")
                connection.shutdown(socket.SHUT_WR)
                with connection.makefile("rb") as reader:
                    self.assertEqual(read_reply(reader)["error"], "incomplete_line")
                    self.assertEqual(reader.readline(), b"")
            with socket.create_connection(address, timeout=2) as connection:
                with connection.makefile("rb") as reader:
                    self.assertEqual(exchange(connection, reader, "5")["error"], "invalid_value")
                    self.assertEqual(backend.targets, [])
                    self.assertEqual(exchange(connection, reader, "0.75")["feedback_counts"], 7500)

    def test_partial_line_times_out_without_motion(self):
        with running_server(client_timeout=0.15) as (address, backend):
            with socket.create_connection(address, timeout=2) as connection:
                connection.sendall(b"0.")
                with connection.makefile("rb") as reader:
                    self.assertEqual(read_reply(reader)["error"], "client_timeout")
                    self.assertEqual(reader.readline(), b"")
            self.assertEqual(backend.targets, [])

    def test_motion_failure_stops_server_and_never_executes_queued_target(self):
        class FailingBackend(RecordingBackend):
            def start_absolute(self, target, config):
                self.targets.append(target)
                raise OSError("simulated drive fault")

        backend = FailingBackend()
        with running_server(backend, expected_error=MotionCommandError) as (address, _):
            with socket.create_connection(address, timeout=2) as connection:
                connection.sendall(b"0.5\n1\n")
                with connection.makefile("rb") as reader:
                    self.assertEqual(read_reply(reader)["error"], "motion_error")
                    self.assertEqual(reader.readline(), b"")
        self.assertEqual(backend.targets, [5000])
        self.assertGreaterEqual(backend.stops, 1)
        self.assertFalse(backend.ready)

    def test_disconnection_during_motion_finishes_accepted_target_before_next_client(self):
        entered, finish = Event(), Event()

        class DelayedBackend(RecordingBackend):
            pending = None

            def start_absolute(self, target, config):
                self.targets.append(target)
                self.pending = target
                entered.set()

            def read_status(self):
                if self.pending is not None and finish.is_set():
                    self.position = self.pending
                    self.pending = None
                state = super().read_status()
                return replace(state, operating=self.pending is not None,
                               in_position=self.pending is None,
                               operation_complete=self.pending is None)

        backend = DelayedBackend()
        try:
            with running_server(backend) as (address, _):
                with socket.create_connection(address, timeout=2) as connection:
                    connection.sendall(b"0.5\n")
                    self.assertTrue(entered.wait(2))
                finish.set()
                with socket.create_connection(address, timeout=2) as connection:
                    with connection.makefile("rb") as reader:
                        self.assertEqual(exchange(connection, reader, "0.5")["feedback_counts"], 5000)
                self.assertEqual(backend.targets, [5000])
        finally:
            finish.set()


class TcpProcessTests(unittest.TestCase):
    def test_main_receiver_and_dummy_sender_as_separate_processes(self):
        root = Path(__file__).resolve().parents[1]
        lines = queue.Queue()
        output = []
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        receiver = subprocess.Popen(
            [sys.executable, "-u", "main.py", "--tcp-server", "--host", "127.0.0.1",
             "--port", "0", "--client-timeout", "5", "--once",
             "--config", "position_board/axis_config.utility2.json"],
            cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, creationflags=flags)

        def read_output():
            for line in receiver.stdout:
                output.append(line)
                lines.put(line)
            lines.put(None)

        reader = Thread(target=read_output, daemon=True)
        reader.start()
        try:
            while True:
                line = lines.get(timeout=5)
                self.assertIsNotNone(line, "".join(output))
                match = re.search(r"TCP listening on 127\.0\.0\.1:(\d+)", line)
                if match:
                    break
            sender = subprocess.run(
                [sys.executable, "dummy_tcp_client.py", "--port", match.group(1),
                 "--interval", "0", "--fragment-bytes", "1", "--include-invalid"],
                cwd=root, capture_output=True, text=True, timeout=10, creationflags=flags)
            self.assertEqual(sender.returncode, 0, sender.stdout + sender.stderr)
            replies = [json.loads(line)["response"] for line in sender.stdout.splitlines()
                       if line.startswith("{")]
            self.assertEqual([reply["feedback_counts"] for reply in replies if reply["ok"]],
                             [11179010, 6984706, 2790402, -1403902, -5598206])
            self.assertEqual([reply["target_counts"] for reply in replies if reply["ok"]],
                             [11179010, 6984706, 2790402, -1403902, -5598206])
            self.assertEqual(sum(not reply["ok"] for reply in replies), 6)
            self.assertIn("PASS: 11 responses", sender.stdout)
            self.assertEqual(receiver.wait(timeout=5), 0, "".join(output))
            self.assertIn("SIMULATION", "".join(output))
        finally:
            if receiver.poll() is None:
                receiver.terminate()
                try:
                    receiver.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    receiver.kill()
                    receiver.wait(timeout=3)
            reader.join(timeout=3)
            receiver.stdout.close()


if __name__ == "__main__":
    unittest.main()
