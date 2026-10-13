import contextlib
import io
import json
import socket
import unittest
from unittest.mock import patch

import main
from servo_axis import AxisStatus, SimulatedBackend
from tcp_input import PositionTCPServer


BENCH_CONFIG = str(main.Path(__file__).resolve().parents[1] / "position_board" / "axis_config.utility2.json")


class CliTests(unittest.TestCase):
    def test_motor_test_requires_explicit_arm_and_fixed_single_axis(self):
        for options in ([], ["--axis", "2", "--enable-motion"],
                        ["--tcp-server"], ["--check-api"]):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()), \
                    patch("main.MrMc240nBackend") as backend, self.assertRaises(SystemExit):
                main.main(["--motor-test", *options])
            backend.assert_not_called()

    def test_motor_test_status_never_prepares_or_arms(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                patch("main.MrMc240nBackend") as backend, patch("main.prepare_motor_test") as prepare:
            backend.return_value.read_status.return_value = AxisStatus(
                2790402, True, False, False, True, True)
            self.assertEqual(main.main(["--motor-test", "--status"]), 0)
        prepare.assert_not_called()
        backend.return_value.set_servo.assert_not_called()

    def test_motor_test_prepares_then_moves_and_releases_servo(self):
        fake = SimulatedBackend(2790402)
        fake.dll_path = "mock.dll"

        def prepare(backend, config, expected):
            self.assertTrue(backend.opened)
            self.assertFalse(backend.ready)
            self.assertEqual(config.speed, 419430)
            self.assertEqual(expected[1, 0x0241], 1)

        with contextlib.redirect_stdout(io.StringIO()) as output, \
                patch("main.MrMc240nBackend", return_value=fake), \
                patch("main.prepare_motor_test", side_effect=prepare) as prepared:
            config = str(main.Path(__file__).resolve().parents[1] / "position_board" / "axis_config.utility2.json")
            self.assertEqual(main.main(["--motor-test", "--config", config, "--enable-motion", "0.25", "0.5"]), 0)
        prepared.assert_called_once()
        self.assertIn("MOVING: input=0.25, target=6984706", output.getvalue())
        self.assertIn("full 0..1 travel=4 revolutions (1440.0000 deg)", output.getvalue())
        self.assertNotIn("less than 1 degree", output.getvalue())
        self.assertEqual(fake.position, 2790402)
        self.assertFalse(fake.ready)
        self.assertFalse(fake.opened)

    def test_motor_test_tcp_binds_prepares_arms_and_executes_received_values(self):
        fake = SimulatedBackend(2790402)
        fake.dll_path = "mock.dll"
        events = []
        enter = PositionTCPServer.__enter__
        serve_forever = PositionTCPServer.serve_forever

        def bind(server):
            result = enter(server)
            events.append("bound")
            return result

        def prepare(backend, config, expected):
            self.assertEqual(events, ["bound"])
            self.assertTrue(backend.opened)
            self.assertFalse(backend.ready)
            self.assertEqual(expected[1, 0x0241], 1)
            events.append("prepared")

        def serve(server, *, once):
            self.assertEqual(events, ["bound", "prepared"])
            self.assertTrue(fake.ready)
            self.assertTrue(once)
            # Small requests/replies fit in the socket buffers, so no test thread is needed.
            with socket.create_connection(server.address, timeout=2) as client:
                client.sendall(b"0.25\n0.75\n0.5\n")
                client.shutdown(socket.SHUT_WR)
                serve_forever(server, once=True)
                with client.makefile("rb") as reader:
                    replies = [json.loads(reader.readline()) for _ in range(3)]
                    self.assertTrue(all(reply["ok"] for reply in replies))
                    self.assertEqual([reply["feedback_counts"] for reply in replies],
                                     [6984706, -1403902, 2790402])
                    self.assertEqual(reader.readline(), b"")
            events.append("served")

        with contextlib.redirect_stdout(io.StringIO()) as output, \
                patch("main.logging.basicConfig"), \
                patch("main.MrMc240nBackend", return_value=fake), \
                patch("main.prepare_motor_test", side_effect=prepare), \
                patch.object(PositionTCPServer, "__enter__", autospec=True, side_effect=bind), \
                patch.object(PositionTCPServer, "serve_forever", autospec=True, side_effect=serve):
            result = main.main(["--motor-test", "--enable-motion", "--config", BENCH_CONFIG,
                                "--tcp-server", "--port", "0", "--once"])
        self.assertEqual(result, 0)
        self.assertEqual(events, ["bound", "prepared", "served"])
        self.assertIn("full 0..1 travel=4 revolutions", output.getvalue())
        self.assertIn("TCP listening on 127.0.0.1:", output.getvalue())
        self.assertFalse(fake.ready)
        self.assertFalse(fake.opened)

    def test_motor_test_tcp_bind_failure_never_prepares_or_arms(self):
        fake = SimulatedBackend(2790402)
        fake.dll_path = "mock.dll"
        with socket.socket() as occupied, contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()) as error, \
                patch("main.logging.basicConfig"), \
                patch("main.MrMc240nBackend", return_value=fake), \
                patch("main.prepare_motor_test") as prepare, \
                patch.object(fake, "set_servo", wraps=fake.set_servo) as servo:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen(1)
            result = main.main(["--motor-test", "--enable-motion", "--config", BENCH_CONFIG,
                                "--tcp-server", "--port", str(occupied.getsockname()[1])])
        self.assertEqual(result, 1)
        self.assertIn("Error:", error.getvalue())
        prepare.assert_not_called()
        servo.assert_not_called()
        self.assertFalse(fake.opened)

    def test_motor_test_tcp_prepare_failure_closes_listener_without_arming(self):
        fake = SimulatedBackend(2790402)
        fake.dll_path = "mock.dll"
        servers = []

        def make_server(*args, **kwargs):
            server = PositionTCPServer(*args, **kwargs)
            servers.append(server)
            return server

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                patch("main.logging.basicConfig"), \
                patch("main.MrMc240nBackend", return_value=fake), \
                patch("main.PositionTCPServer", side_effect=make_server), \
                patch("main.prepare_motor_test", side_effect=RuntimeError("parameter mismatch")), \
                patch.object(fake, "set_servo", wraps=fake.set_servo) as servo:
            result = main.main(["--motor-test", "--enable-motion", "--config", BENCH_CONFIG,
                                "--tcp-server", "--port", "0"])
        self.assertEqual(result, 1)
        self.assertEqual(len(servers), 1)
        with self.assertRaises(RuntimeError):
            _ = servers[0].address
        servo.assert_not_called()
        self.assertFalse(fake.opened)

    def test_default_is_simulation(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("main.MrMc240nBackend") as real:
            self.assertEqual(main.main(["0", "0.5", "1"]), 0)
        real.assert_not_called()
        self.assertIn("SIMULATION", output.getvalue())
        self.assertIn("0=2794600, 1=2786203", output.getvalue())
        for target in (2794600, 2790402, 2786203):
            self.assertIn(f"target={target}, feedback={target}", output.getvalue())

    def test_batch_is_validated_before_any_connection(self):
        with contextlib.redirect_stderr(io.StringIO()), patch("main.SimulatedBackend") as backend:
            self.assertEqual(main.main(["0.5", "nan"]), 1)
        backend.assert_not_called()

    def test_hardware_movement_requires_explicit_configuration_and_arm(self):
        for args in (["--hardware", "0.5"],
                     ["--hardware", "--dll", "unused.dll", "0.5"],
                     ["--hardware", "--dll", "unused.dll", "--config", "unused.json", "0.5"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    patch("main.MrMc240nBackend") as backend, self.assertRaises(SystemExit):
                main.main(args)
            backend.assert_not_called()

    def test_tcp_argument_errors_are_rejected_before_connecting(self):
        for args in (["--tcp-server", "0.5"], ["--tcp-server", "--status"],
                     ["--host", "127.0.0.1"], ["--once"],
                     ["--tcp-server", "--port", "65536"],
                     ["--tcp-server", "--client-timeout", "nan"],
                     ["--tcp-server", "--hardware", "--dll", "unused.dll"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), \
                    patch("main.NormalizedAxis") as axis, self.assertRaises(SystemExit):
                main.main(args)
            axis.assert_not_called()

    def test_check_api_without_dll_path_does_not_open_or_arm(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), patch("main.MrMc240nBackend") as backend:
            backend.return_value.api_info.return_value = {"api_loaded": True}
            self.assertEqual(main.main(["--hardware", "--check-api"]), 0)
        backend.assert_called_once_with(None, board_id=0, axis_number=1, point_number=0)
        backend.return_value.open.assert_not_called()
        backend.return_value.set_servo.assert_not_called()
        self.assertTrue(json.loads(output.getvalue())["api_loaded"])

    def test_status_without_dll_or_config_reads_and_closes_board(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()), \
                patch("main.MrMc240nBackend") as backend:
            backend.return_value.read_status.return_value = AxisStatus(
                2794600, True, False, False, True, True)
            self.assertEqual(main.main(["--hardware", "--status"]), 0)
        self.assertEqual(json.loads(output.getvalue())["position_counts"], 2794600)
        backend.return_value.open.assert_called_once_with()
        backend.return_value.close.assert_called_once_with()
        backend.return_value.set_servo.assert_not_called()

    def test_api_check_rejects_movement_and_simulation_options(self):
        for options in ([], ["--hardware", "0.5"], ["--hardware", "--enable-motion"],
                        ["--hardware", "--tcp-server"], ["--hardware", "--status"],
                        ["--hardware", "--config", "unused.json"]):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()), \
                    patch("main.MrMc240nBackend") as backend, self.assertRaises(SystemExit):
                main.main(["--check-api", *options])
            backend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
