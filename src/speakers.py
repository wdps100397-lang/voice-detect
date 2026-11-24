"""WeSpeaker embeddings with WebRTC VAD and a local enrollment database."""
import numpy as np
import kaldi_native_fbank as knf
import onnxruntime as ort
import webrtcvad

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


def speech_audio(audio, rate=16000):
    pcm = (np.clip(audio, -1, .999969) * 32768).astype("<i2")
    vad = webrtcvad.Vad(2)
    size = rate * 30 // 1000
    frames = [audio[i:i+size] for i in range(0, len(audio)-size+1, size)
              if vad.is_speech(pcm[i:i+size].tobytes(), rate)]
    if not frames or len(frames) * .03 < 2:
        raise ValueError("Need at least 2 seconds of detected speech")
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

    def embedding(self, path):
        _, audio = load_audio(path, 16000)
        if np.max(abs(audio)) >= .999:
            raise ValueError("Clipped audio. Reduce input gain and record again.")
        audio = speech_audio(audio)
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

    def enroll(self, name, files, database):
        validate_user_id(name)
        if len(files) < 2:
            raise ValueError("Use at least two different recordings per enrollment")
        if len({fingerprint(path) for path in files}) != len(files):
            raise ValueError("Enrollment recordings must be distinct")
        embeddings = np.array([self.embedding(path) for path in files])
        template = embeddings.mean(axis=0)
        norm = np.linalg.norm(template)
        if norm < 1e-8:
            raise ValueError("Inconsistent enrollment recordings")
        template /= norm
        with database_lock(database):
            data = self.database(database)
            if name in data["speakers"]:
                raise ValueError("Speaker already enrolled. Choose a new name or back up and edit the database.")
            data["speakers"][name] = dict(template=template.tolist(), recordings=len(files))
            save_json(database, data)
        return dict(status="enrolled", user_id=name, recordings=len(files))

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

    def identify(self, path, database, threshold, margin):
        data = self.database(database)
        if not data["speakers"]:
            raise ValueError("Enroll speakers first")
        try:
            embedding = self.embedding(path)
        except ValueError as error:
            return dict(status="unusable_audio", user_id="", reason=str(error))
        scores = sorted(((name, float(embedding @ np.array(row["template"])))
                         for name, row in data["speakers"].items()), key=lambda row: row[1], reverse=True)
        best_name, best_score = scores[0]
        gap = best_score - scores[1][1] if len(scores) > 1 else 2.
        accepted = best_score >= threshold and gap >= margin
        if accepted and (len(best_name) > 64 or best_name != best_name.strip() or best_name == "UNKNOWN"):
            raise ValueError("Enrolled key must be a valid Project A user_id")
        return dict(status="recognized" if accepted else "unknown", user_id=best_name if accepted else "",
                    score=best_score, score_gap=gap, threshold=threshold, scores=dict(scores))
