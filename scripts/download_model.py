"""Download the upstream WeSpeaker ONNX model at a pinned revision."""
from pathlib import Path
import hashlib
import json
import os
from urllib.request import urlretrieve

ROOT = Path(__file__).resolve().parents[1]
REVISION = "ff1ac5bca8ef11e90662b879aa923979e0bd277b"
REPO = "Wespeaker/wespeaker-voxceleb-resnet34"
NAME = "voxceleb_resnet34.onnx"
EXPECTED_SHA256 = "9fea6516d7ad6bf0a76c7689f5a49b65d330fad6dde96c91bb4435ffbfe056a1"


def main():
    folder = ROOT / "models"
    folder.mkdir(exist_ok=True)
    target = folder / NAME
    expected = EXPECTED_SHA256
    def digest(path):
        with path.open("rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()
    if target.exists() and digest(target) == expected:
        print("Model verified")
        return
    temporary = target.with_suffix(".download")
    try:
        urlretrieve(f"https://huggingface.co/{REPO}/resolve/{REVISION}/{NAME}", temporary)
        if digest(temporary) != expected:
            raise ValueError("Model checksum mismatch")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    (folder / "model_info.json").write_text(json.dumps(dict(
        repository=REPO, revision=REVISION, sha256=expected), indent=2), encoding="utf8")
    print("Model downloaded and verified")


if __name__ == "__main__":
    main()
