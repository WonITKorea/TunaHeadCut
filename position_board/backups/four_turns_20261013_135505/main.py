"""Command-line entry point. The default backend is a simulator."""

import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path
import sys
import time

from motor_bench import COUNTS_PER_REVOLUTION, bench_parameters, motor_degrees, prepare_motor_test
from mr_mc240n import MrMc240nBackend
from servo_axis import AxisConfig, NormalizedAxis, SimulatedBackend, normalized_value
from tcp_input import PositionTCPServer, positive_seconds


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="TunaHeadCut: 0..1 single-axis absolute positioning")
    result.add_argument("values", nargs="*", help="0..1 targets; omit for interactive input")
    result.add_argument("--config", help="JSON file with calibrated endpoint counts and speed")
    result.add_argument("--hardware", action="store_true", help="use the MR-MC240N PCIe API")
    result.add_argument("--motor-test", action="store_true",
                        help="uncoupled motor preset: hardware, local config, verified Flash startup in READY")
    result.add_argument("--dll", help="Mitsubishi API DLL path (default: detect installed API Library)")
    result.add_argument("--board-id", type=int, default=0)
    result.add_argument("--axis", type=int, default=1, help="one selected axis (default: 1)")
    result.add_argument("--point", type=int, default=0, help="point table slot reserved for this app")
    result.add_argument("--enable-motion", action="store_true", help="explicitly arm the hardware axis")
    result.add_argument("--status", action="store_true", help="read status without arming or moving")
    result.add_argument("--check-api", action="store_true", help="load and verify the hardware DLL without opening a board")
    result.add_argument("--tcp-server", action="store_true", help="receive newline-delimited TCP values")
    result.add_argument("--host", help="TCP bind IPv4 address (default: 127.0.0.1)")
    result.add_argument("--port", type=int, help="TCP port (default: 5000; 0 selects a free port)")
    result.add_argument("--client-timeout", type=float, help="TCP input timeout in seconds (default: 30)")
    result.add_argument("--once", action="store_true", help="exit after one TCP client disconnects")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.motor_test:
        if args.check_api or args.tcp_server or (args.board_id, args.axis, args.point) != (0, 1, 0):
            arguments.error("--motor-test supports local input on board 0, axis 1, point 0 only")
        args.hardware = True
        if not args.status and not args.config:
            root = Path(__file__).resolve().parent
            local_config = root / "axis_config.json"
            args.config = str(local_config if local_config.is_file() else
                              root / "position_board" / "axis_config.utility2.json")
    if args.check_api and (not args.hardware or args.status or args.values or args.tcp_server
                          or args.enable_motion or args.config):
        arguments.error("--check-api requires --hardware and cannot be combined with status or movement options")
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
        if not args.status and not args.check_api and not args.config:
            arguments.error("hardware movement requires --config")
        if not args.status and not args.check_api and not args.enable_motion:
            arguments.error("hardware movement requires --enable-motion")
    elif args.enable_motion or args.dll:
        arguments.error("--enable-motion and --dll require --hardware")
    try:
        values = [normalized_value(value) for value in args.values]
        if args.hardware and args.check_api:
            backend = MrMc240nBackend(args.dll, board_id=args.board_id,
                                     axis_number=args.axis, point_number=args.point)
            print(json.dumps(backend.api_info(), indent=2))
            return 0
        # Status is useful before calibration. It does not need a travel config.
        if args.hardware and args.status:
            backend = MrMc240nBackend(args.dll, board_id=args.board_id,
                                     axis_number=args.axis, point_number=args.point)
            backend.open()
            try:
                print(f"API DLL: {backend.dll_path}", file=sys.stderr)
                print(json.dumps(asdict(backend.read_status()), indent=2))
            finally:
                backend.close()
            return 0
        config = (AxisConfig.from_file(args.config) if args.config else
                  AxisConfig(2_786_203, 2_794_600, 1000, reverse_direction=True))
        expected = bench_parameters(config) if args.motor_test else None
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
            if args.hardware:
                print(f"API DLL: {backend.dll_path}", flush=True)
            if args.status:
                print(json.dumps(asdict(axis.status()), indent=2))
                return 0
            if args.motor_test:
                prepare_motor_test(backend, config, expected)
                travel = config.max_position_counts - config.min_position_counts
                print(f"MOTOR SCALE: {COUNTS_PER_REVOLUTION} counts/rev (HG-KR13, gear 1:1); "
                      f"full 0..1 travel={motor_degrees(travel):.4f} deg; "
                      f"speed={config.speed * 60 / COUNTS_PER_REVOLUTION:.4f} rpm", flush=True)
                print("The configured travel is less than 1 degree; shaft rotation may be hard to see.",
                      flush=True)
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
            print("Servo ON: waiting for position to settle...", flush=True)
            axis.arm()
            print(f"READY: feedback={axis.status().position_counts}, speed={config.speed}", flush=True)

            def move(value: float) -> None:
                target = config.target_counts(value)
                started = time.monotonic()
                last_report = started - 1.0
                start_position = axis.status().position_counts if args.motor_test else None
                angle = (f", delta={target - start_position:+d} counts "
                         f"({motor_degrees(target - start_position):+.4f} deg)"
                         if start_position is not None else "")
                print(f"MOVING: input={value:g}, target={target}{angle}", flush=True)

                def progress(state):
                    nonlocal last_report
                    now = time.monotonic()
                    if now - last_report >= 1.0:
                        print(f"  elapsed={now - started:.1f}s, feedback={state.position_counts}, "
                              f"remaining={target - state.position_counts}", flush=True)
                        last_report = now

                position = axis.move_normalized(value, on_progress=progress)
                measured = (f", measured_delta={position - start_position:+d} counts "
                            f"({motor_degrees(position - start_position):+.4f} deg)"
                            if start_position is not None else "")
                print(f"input={value:g}, target={target}, feedback={position}{measured}", flush=True)

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
