"""Command-line entry point. The default backend is a simulator."""

import argparse
from dataclasses import asdict
import json
import logging
import sys

from mr_mc240n import MrMc240nBackend
from servo_axis import AxisConfig, NormalizedAxis, SimulatedBackend, normalized_value
from tcp_input import PositionTCPServer, positive_seconds


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="TunaHeadCut: 0..1 single-axis absolute positioning")
    result.add_argument("values", nargs="*", help="0..1 targets; omit for interactive input")
    result.add_argument("--config", help="JSON file with calibrated endpoint counts and speed")
    result.add_argument("--hardware", action="store_true", help="use the MR-MC240N PCIe API")
    result.add_argument("--dll", help="absolute path to the matching Mitsubishi API DLL")
    result.add_argument("--board-id", type=int, default=0)
    result.add_argument("--axis", type=int, default=1, help="one selected axis (default: 1)")
    result.add_argument("--point", type=int, default=0, help="point table slot reserved for this app")
    result.add_argument("--enable-motion", action="store_true", help="explicitly arm the hardware axis")
    result.add_argument("--status", action="store_true", help="read status without arming or moving")
    result.add_argument("--tcp-server", action="store_true", help="receive newline-delimited TCP values")
    result.add_argument("--host", help="TCP bind IPv4 address (default: 127.0.0.1)")
    result.add_argument("--port", type=int, help="TCP port (default: 5000; 0 selects a free port)")
    result.add_argument("--client-timeout", type=float, help="TCP input timeout in seconds (default: 30)")
    result.add_argument("--once", action="store_true", help="exit after one TCP client disconnects")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.status and args.values:
        arguments.error("--status cannot be combined with movement values")
    if args.tcp_server and (args.status or args.values):
        arguments.error("--tcp-server cannot be combined with --status or positional values")
    if not args.tcp_server and (
            args.host is not None or args.port is not None or args.client_timeout is not None or args.once):
        arguments.error("--host, --port, --client-timeout and --once require --tcp-server")
    if args.tcp_server:
        args.host = args.host if args.host is not None else "127.0.0.1"
        args.port = args.port if args.port is not None else 5000
        args.client_timeout = args.client_timeout if args.client_timeout is not None else 30.0
        if not 0 <= args.port <= 65535:
            arguments.error("--port must be between 0 and 65535")
        try:
            positive_seconds(args.client_timeout)
        except ValueError as exc:
            arguments.error(str(exc))
    if args.hardware:
        if not args.dll:
            arguments.error("--hardware requires --dll")
        if not args.status and not args.config:
            arguments.error("hardware movement requires --config")
        if not args.status and not args.enable_motion:
            arguments.error("hardware movement requires --enable-motion")
    elif args.enable_motion or args.dll:
        arguments.error("--enable-motion and --dll require --hardware")
    try:
        values = [normalized_value(value) for value in args.values]
        # Status is useful before calibration. It does not need a travel config.
        if args.hardware and args.status:
            backend = MrMc240nBackend(args.dll, board_id=args.board_id,
                                     axis_number=args.axis, point_number=args.point)
            backend.open()
            try:
                print(json.dumps(asdict(backend.read_status()), indent=2))
            finally:
                backend.close()
            return 0
        config = (AxisConfig.from_file(args.config) if args.config else
                  AxisConfig(2_786_203, 2_794_600, 1000, reverse_direction=True))
        if args.hardware:
            backend = MrMc240nBackend(args.dll, board_id=args.board_id,
                                     axis_number=args.axis, point_number=args.point)
            print(f"HARDWARE: board {args.board_id}, axis {args.axis}; "
                  f"0={config.target_counts(0)}, 1={config.target_counts(1)}")
        else:
            backend = SimulatedBackend(config.target_counts(0))
            print("SIMULATION: no hardware connection; "
                  f"0={config.target_counts(0)}, 1={config.target_counts(1)}")
        with NormalizedAxis(backend, config) as axis:
            if args.status:
                print(json.dumps(asdict(axis.status()), indent=2))
                return 0
            if args.tcp_server:
                logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                                    format="[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
                with PositionTCPServer(axis, args.host, args.port,
                                       client_timeout_s=args.client_timeout) as server:
                    # Bind successfully before enabling servo torque.
                    axis.arm()
                    host, port = server.address
                    print(f"TCP listening on {host}:{port}", flush=True)
                    server.serve_forever(once=args.once)
                return 0
            axis.arm()

            def move(value: float) -> None:
                target = config.target_counts(value)
                position = axis.move_normalized(value)
                print(f"input={value:g}, target={target}, feedback={position}")

            if values:
                for value in values:
                    move(value)
            else:
                print("Enter a value from 0 to 1, 'status', or 'q'. Ctrl+C stops and exits.")
                while True:
                    try:
                        value = input("position> ").strip()
                    except EOFError:
                        break
                    if value.lower() in ("q", "quit", "exit"):
                        break
                    if value.lower() == "status":
                        print(json.dumps(asdict(axis.status()), indent=2))
                        continue
                    try:
                        number = normalized_value(value)
                    except ValueError as exc:
                        print(exc, file=sys.stderr)
                        continue
                    move(number)
        return 0
    except KeyboardInterrupt:
        print("Interrupted; session cleanup requested.", file=sys.stderr)
        return 130
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
