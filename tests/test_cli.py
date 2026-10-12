import contextlib
import io
import unittest
from unittest.mock import patch

import main


class CliTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
