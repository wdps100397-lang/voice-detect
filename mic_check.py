"""Compatibility entry point for the original recording utility."""
from src.capture import list_inputs, record, main
import sys

if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    raise SystemExit(main())
