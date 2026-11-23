import hashlib
import json
import os
from pathlib import Path
import tempfile
from contextlib import contextmanager
import msvcrt

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly
from math import gcd

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def database_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+b") as lock:
        lock.seek(0, 2)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise ValueError("Another enrollment is writing this database. Retry after it finishes.") from error
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def save_json(path, value):
    """Replace a complete JSON document atomically, including on Windows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf8"))


def fingerprint(path):
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def load_audio(path, target_rate=None):
    rate, audio = wavfile.read(path)
    if audio.ndim != 1:
        raise ValueError(f"{path}: provide a mono WAV")
    if np.issubdtype(audio.dtype, np.signedinteger):
        audio = audio.astype(np.float64) / (np.iinfo(audio.dtype).max + 1)
    elif audio.dtype == np.uint8:
        audio = (audio.astype(np.float64) - 128) / 128
    else:
        audio = audio.astype(np.float64)
    if not len(audio) or not np.isfinite(audio).all():
        raise ValueError(f"{path}: empty or invalid audio")
    if target_rate and target_rate != rate:
        divisor = gcd(rate, target_rate)
        audio = resample_poly(audio, target_rate // divisor, rate // divisor)
        rate = target_rate
    return rate, audio


def load_tracks(files):
    tracks = [load_audio(path) for path in files]
    if len(tracks) != 3 or len({rate for rate, _ in tracks}) != 1:
        raise ValueError("Provide three mono WAVs with the same sample rate")
    return tracks[0][0], [audio for _, audio in tracks]
