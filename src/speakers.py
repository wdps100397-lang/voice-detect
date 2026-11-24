"""WeSpeaker embeddings with WebRTC VAD and a local enrollment database."""
from math import gcd

import numpy as np
import kaldi_native_fbank as knf
import onnxruntime as ort
import webrtcvad
from scipy.signal import resample_poly

from src.common import ROOT, fingerprint, load_audio, read_json, save_json, database_lock

MODEL = ROOT / "models" / "voxceleb_resnet34.onnx"


def validate_user_id(name):
    if not name.strip() or name != name.strip() or len(name) > 64 or name == "UNKNOWN":
        raise ValueError("Use a nonempty Project A user_id up to 64 characters, excluding UNKNOWN")


def filterbank(audio):
    options = knf.FbankOptions()
    options.frame_opts.samp_freq = 16000
    options.frame_opts.dither = 0
    options.frame_opts.window_type = "hamming"
    options.mel_opts.num_bins = 80
    options.mel_opts.debug_mel = False
    fbank = knf.OnlineFbank(options)
    fbank.accept_waveform(16000, (audio * 32768).tolist())
    features = np.array([fbank.get_frame(i) for i in range(fbank.num_frames_ready)],
                        dtype=np.float32)
    return features - features.mean(axis=0)


class InsufficientSpeech(ValueError):
    def __init__(self, detected_seconds, required_seconds):
        self.detected_seconds = detected_seconds
        super().__init__(f"Need at least {required_seconds:g} seconds of detected speech")


def speech_audio(audio, rate=16000, *, min_speech_seconds=2.0):
    pcm = (np.clip(audio, -1, .999969) * 32768).astype("<i2")
    vad = webrtcvad.Vad(2)
    size = rate * 30 // 1000
    frames = [audio[i:i+size] for i in range(0, len(audio)-size+1, size)
              if vad.is_speech(pcm[i:i+size].tobytes(), rate)]
    detected = round(len(frames) * .03, 2)
    if not frames or detected < min_speech_seconds:
        raise InsufficientSpeech(detected, min_speech_seconds)
    return np.concatenate(frames)


class SpeakerEngine:
    def __init__(self, model=MODEL):
        if not model.exists():
            raise ValueError("Model missing. Run scripts/download_model.py")
        self.model_hash = fingerprint(model)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(str(model), sess_options=options,
                                           providers=["CPUExecutionProvider"])

    def embedding(self, path, *, min_speech_seconds=2.0):
        _, audio = load_audio(path, 16000)
        if np.max(abs(audio)) >= .999:
            raise ValueError("Clipped audio. Reduce input gain and record again.")
        audio = speech_audio(audio, min_speech_seconds=min_speech_seconds)
        return self.embedding_from_speech(audio)

    def embedding_from_speech(self, audio):
        features = filterbank(audio)
        embedding = self.session.run(None, {self.session.get_inputs()[0].name: features[None]})[0].reshape(-1)
        norm = np.linalg.norm(embedding)
        if not np.isfinite(embedding).all() or norm < 1e-8:
            raise ValueError("Invalid model output")
        return embedding / norm

    def database(self, path):
        data = read_json(path) if path.exists() else dict(schema=1, model_sha256=self.model_hash, speakers={})
        if data.get("schema") != 1 or data["model_sha256"] != self.model_hash:
            raise ValueError("Database model mismatch. Re-enroll with this model.")
        return data

    def enroll(self, name, files, database, *, source_hashes=None):
        return self.store_samples(name, files, database, append=False, source_hashes=source_hashes)

    def append_samples(self, name, files, database, *, source_hashes=None):
        return self.store_samples(name, files, database, append=True, source_hashes=source_hashes)

    def store_samples(self, name, files, database, *, append, source_hashes):
        validate_user_id(name)
        if append and not files:
            raise ValueError("請先加入至少一段新錄音")
        if not append and len(files) < 2:
            raise ValueError("Use at least two different recordings per enrollment")
        clip_hashes = [fingerprint(path) for path in files]
        source_hashes = list(source_hashes) if source_hashes is not None else clip_hashes
        if len(source_hashes) != len(files):
            raise ValueError("Provide a source fingerprint for each enrollment recording")
        if min(len(set(clip_hashes)), len(set(source_hashes))) < len(files):
            raise ValueError("Enrollment recordings must be distinct")
        total = np.sum([self.embedding(path) for path in files], axis=0, dtype=np.float64)
        count = len(files)
        with database_lock(database):
            data = self.database(database)
            speakers = data["speakers"]
            previous = speakers.get(name)
            if append:
                if previous is None:
                    raise ValueError(f"找不到聲紋 ID：{name}")
                old_hashes = previous.get("recording_hashes", [])
                if set(source_hashes).intersection(old_hashes):
                    raise ValueError("有錄音已用於這位講者，請改用新錄音")
                old_count = previous.get("recordings", 1)
                # Older databases stored only a normalized template.
                old_total = previous.get("embedding_sum")
                if old_total is None:
                    old_total = np.asarray(previous["template"]) * old_count
                total = total + old_total
                count += old_count
                source_hashes = [*old_hashes, *source_hashes]
            elif previous is not None:
                raise ValueError("講者 ID 已存在，請使用「補樣本」")
            norm = np.linalg.norm(total)
            if not np.isfinite(norm) or norm < 1e-8:
                raise ValueError("Inconsistent enrollment recordings")
            speakers[name] = dict(previous or {}, template=(total / norm).tolist(), recordings=count,
                                  embedding_sum=total.tolist(), recording_hashes=source_hashes)
            save_json(database, data)
        return dict(status="updated" if append else "enrolled", user_id=name, recordings=count)

    def rename(self, database, current, updated):
        validate_user_id(updated)
        with database_lock(database):
            data = self.database(database)
            speakers = data["speakers"]
            if current not in speakers:
                raise ValueError(f"找不到聲紋 ID：{current}")
            if updated == current:
                return
            if updated in speakers:
                raise ValueError(f"聲紋 ID 已存在：{updated}")
            speakers[updated] = speakers.pop(current)
            save_json(database, data)

    def identify(self, path, database, threshold, margin, *, min_speech_seconds=None):
        data = self.database(database)
        if not data["speakers"]:
            raise ValueError("Enroll speakers first")
        try:
            if min_speech_seconds is None:
                embedding = self.embedding(path)
            else:
                embedding = self.embedding(path, min_speech_seconds=min_speech_seconds)
        except InsufficientSpeech as error:
            if min_speech_seconds is None:
                return dict(status="unusable_audio", user_id="", reason=str(error))
            return dict(status="insufficient_speech", user_id="", reason=str(error),
                        detected_speech_seconds=error.detected_seconds)
        except ValueError as error:
            return dict(status="unusable_audio", user_id="", reason=str(error))
        return self.match_embedding(embedding, data, threshold, margin)

    def identify_speech(self, audio, rate, database, threshold, margin):
        """Score frames already selected by the live voice activity detector."""
        data = self.database(database)
        if not data["speakers"]:
            raise ValueError("Enroll speakers first")
        if np.max(abs(audio)) >= .999:
            return dict(status="unusable_audio", user_id="", reason="Clipped audio")
        if rate != 16000:
            divisor = gcd(rate, 16000)
            audio = resample_poly(audio, 16000 // divisor, rate // divisor)
        try:
            embedding = self.embedding_from_speech(audio)
        except ValueError as error:
            return dict(status="unusable_audio", user_id="", reason=str(error))
        return self.match_embedding(embedding, data, threshold, margin)

    @staticmethod
    def match_embedding(embedding, data, threshold, margin):
        scores = sorted(((name, float(embedding @ np.array(row["template"])))
                         for name, row in data["speakers"].items()), key=lambda row: row[1], reverse=True)
        best_name, best_score = scores[0]
        gap = best_score - scores[1][1] if len(scores) > 1 else 2.
        accepted = best_score >= threshold and gap >= margin
        if accepted and (len(best_name) > 64 or best_name != best_name.strip() or best_name == "UNKNOWN"):
            raise ValueError("Enrolled key must be a valid Project A user_id")
        return dict(status="recognized" if accepted else "unknown", user_id=best_name if accepted else "",
                    score=best_score, score_gap=gap, threshold=threshold, scores=dict(scores))
