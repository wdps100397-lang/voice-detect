"""Command line entry point for three-USB-microphone experiments."""
import argparse
from pathlib import Path
import sys

from src.common import ROOT, save_json


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("devices", help="List audio inputs")
    sub.add_parser("configure", help="Disable USB input AGC and system effects, and match gain")
    sub.add_parser("test", help="Run local synthetic tests without recording or external services")
    live = sub.add_parser("live", help="Continuous real speaker identity worker for the ROS node")
    live.add_argument("--config", type=Path, default=ROOT / "config" / "live.json")
    capture = sub.add_parser("record", help="Record one to three separate inputs")
    capture.add_argument("--devices", type=int, nargs="+", required=True)
    capture.add_argument("--seconds", type=int, default=20)
    modes = capture.add_mutually_exclusive_group()
    modes.add_argument("--exclusive", dest="exclusive", action="store_true",
                       help="Use WASAPI exclusive capture (default)")
    modes.add_argument("--shared", dest="exclusive", action="store_false",
                       help="Use shared capture for comparison")
    capture.set_defaults(exclusive=True)
    calibrate = sub.add_parser("calibrate", help="Calibrate clock offsets using a stationary broadband sound")
    calibrate.add_argument("--files", nargs="+", required=True)
    calibrate.add_argument("--start", type=float, required=True)
    calibrate.add_argument("--end", type=float, required=True)
    calibrate.add_argument("--distances", type=float, nargs="+", required=True, help="One source distance per microphone")
    calibrate.add_argument("--output", type=Path, required=True)
    locate = sub.add_parser("locate", help="Estimate horizontal direction from a calibrated recording")
    locate.add_argument("--files", nargs="+", required=True)
    locate.add_argument("--calibration", type=Path, required=True)
    locate.add_argument("--config", type=Path, default=ROOT / "config" / "array.json")
    locate.add_argument("--start", type=float, required=True)
    locate.add_argument("--end", type=float, required=True)
    locate.add_argument("--output", type=Path)
    analyze = sub.add_parser("analyze", help="Get direction and speaker for the same event")
    analyze.add_argument("--files", nargs="+", required=True)
    analyze.add_argument("--calibration", type=Path)
    analyze.add_argument("--speaker-recognition", action=argparse.BooleanOptionalAction, default=True)
    analyze.add_argument("--localization", action=argparse.BooleanOptionalAction, default=True)
    analyze.add_argument("--config", type=Path, default=ROOT / "config" / "array.json")
    analyze.add_argument("--start", type=float, required=True)
    analyze.add_argument("--end", type=float, required=True)
    analyze.add_argument("--database", type=Path, default=ROOT / "data" / "speakers.json")
    analyze.add_argument("--threshold", type=float, default=.65)
    analyze.add_argument("--margin", type=float, default=.08)
    analyze.add_argument("--output", type=Path, required=True)
    analyze.add_argument("--utterance-id", help="Shared utterance ID from the external STT system")
    analyze.add_argument("--stt-file", type=Path, help="Project A JSON with utterance_id and final text")
    analyze.add_argument("--observed-at", type=float, help="Actual observation time in Unix seconds")
    analyze.add_argument("--identity-output", type=Path, help="Native speaker/identity data JSON, or null")
    analyze.add_argument("--input-output", type=Path, help="Native agent/input data JSON, or null")
    enroll = sub.add_parser("enroll", help="Enroll a speaker using multiple mono WAVs")
    enroll.add_argument("--user-id", "--name", dest="name", required=True, help="Project A users ID")
    enroll.add_argument("--files", nargs="+", required=True)
    identify = sub.add_parser("identify", help="Recognize a speaker or return unknown")
    identify.add_argument("--file", required=True)
    identify.add_argument("--threshold", type=float, default=.65)
    identify.add_argument("--margin", type=float, default=.08)
    for command in (enroll, identify):
        command.add_argument("--database", type=Path, default=ROOT / "data" / "speakers.json")
    args = parser.parse_args()
    if args.command == "live":
        from src.live import run_live
        import json
        def emit(event):
            print(json.dumps(event, ensure_ascii=False, allow_nan=False), flush=True)
        try:
            run_live(args.config, emit)
        except KeyboardInterrupt:
            return 0
        except Exception as error:
            emit(dict(kind="error", error=str(error)))
            return 1
        return 0
    if args.command == "test":
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    if args.command == "configure":
        from src.hardware import configure
        print(configure())
        return 0
    if args.command in ("devices", "record"):
        from src.capture import list_inputs, record
        if args.command == "devices":
            list_inputs()
            return 0
        if not 1 <= len(args.devices) <= 4 or len(set(args.devices)) != len(args.devices):
            raise ValueError("Select one to four distinct inputs")
        if min(args.devices) < 0 or not 5 <= args.seconds <= 60:
            raise ValueError("Use nonnegative device indices and 5 to 60 seconds")
        return record(args.devices, args.seconds, args.exclusive)
    if args.command == "calibrate":
        from src.localization import calibration
        result = calibration(args.files, args.start, args.end, args.distances, args.output)
    elif args.command in ("locate", "analyze"):
        if args.command == "analyze":
            from src.events import analyze_event
            from src.common import read_json
            result = analyze_event(args.files, args.config, args.calibration, args.start, args.end,
                                   args.database, args.threshold, args.margin,
                                   utterance_id=args.utterance_id,
                                   stt=read_json(args.stt_file) if args.stt_file else None,
                                   observed_at=args.observed_at,
                                   enable_speaker=args.speaker_recognition,
                                   enable_localization=args.localization)
        else:
            from src.localization import locate
            result = locate(args.files, args.config, args.calibration, args.start, args.end)
        if args.output:
            save_json(args.output, result)
        if args.command == "analyze":
            if args.identity_output:
                save_json(args.identity_output, result["speaker_identity"])
            if args.input_output:
                save_json(args.input_output, result["agent_input"])
    else:
        from src.speakers import SpeakerEngine
        engine = SpeakerEngine()
        if args.command == "enroll":
            result = engine.enroll(args.name, args.files, args.database)
        else:
            if not -1 <= args.threshold <= 1 or not 0 <= args.margin <= 2:
                raise ValueError("Threshold must be -1..1 and margin 0..2")
            result = engine.identify(args.file, args.database, args.threshold, args.margin)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    try:
        raise SystemExit(main())
    except (ValueError, OSError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1)
