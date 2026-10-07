"""Dummy sender: send one number per line, wait for completion, print the reply."""

import argparse
import json
import math
import socket
import sys
import time


DEFAULT_VALUES = ["0", "0.25", "0.5", "0.75", "1"]
INVALID_VALUES = ["-0.1", "1.1", "nan", "inf", "abc", ""]


def exchange(connection: socket.socket, reader, value: str, *, fragment_bytes: int = 0) -> dict:
    if "\n" in value or "\r" in value:
        raise ValueError("Send one number at a time, without embedded line breaks.")
    payload = (value + "\n").encode("ascii")
    size = fragment_bytes or len(payload)
    if size < 1:
        raise ValueError("fragment_bytes must be nonnegative.")
    for offset in range(0, len(payload), size):
        connection.sendall(payload[offset:offset + size])
    response = reader.readline(4097)
    if not response or len(response) > 4096 or not response.endswith(b"\n"):
        raise RuntimeError("Connection ended or response was incomplete/too long.")
    result = json.loads(response)
    if not isinstance(result, dict) or type(result.get("ok")) is not bool:
        raise ValueError("Malformed server response.")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Send dummy 0..1 values to TunaHeadCut over TCP")
    parser.add_argument("values", nargs="*", help="default: 0 0.25 0.5 0.75 1")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--timeout", type=float, default=40.0, help="response timeout in seconds")
    parser.add_argument("--interval", type=float, default=0.2, help="pause after each response")
    parser.add_argument("--fragment-bytes", type=int, default=0, help="split each request into writes")
    parser.add_argument("--include-invalid", action="store_true", help="also verify six rejected inputs")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    if not math.isfinite(args.interval) or args.interval < 0 or args.fragment_bytes < 0:
        parser.error("--interval and --fragment-bytes must be finite and nonnegative")
    values = [(value, True) for value in (args.values or DEFAULT_VALUES)]
    if args.include_invalid:
        values.extend((value, False) for value in INVALID_VALUES)
    try:
        with socket.create_connection((args.host, args.port), timeout=args.timeout) as connection:
            with connection.makefile("rb") as reader:
                for value, expected_ok in values:
                    reply = exchange(connection, reader, value, fragment_bytes=args.fragment_bytes)
                    print(json.dumps({"sent": value, "response": reply}, ensure_ascii=False), flush=True)
                    if reply["ok"] != expected_ok or (
                            not expected_ok and reply.get("error") != "invalid_value"):
                        raise RuntimeError(f"Unexpected response for input {value!r}: {reply}")
                    time.sleep(args.interval)
        print(f"PASS: {len(values)} responses matched expectations.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}. No command was retried.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Sender interrupted. An accepted target may still be executing.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
