"""Local browser workbench for microphone, speaker and direction tests."""
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import shutil
from tempfile import TemporaryDirectory, mkdtemp
import threading
from urllib.parse import quote, unquote, urlsplit
import webbrowser

import numpy as np
import sounddevice as sd

from src.capture import capture_to_directory
from src.common import ROOT, database_lock, fingerprint, load_audio, read_json, save_json
from src.ui_audio import localize_run, new_run, speaker_clip, tracks_in


WEB = Path(__file__).with_name("web")
RUNS = ROOT / "runs"
SETTINGS = ROOT / "data" / "ui_settings.json"
TEST_AUDIO = ("broadband_30s.wav", "noise_vs_sweep_29s.wav")
MAX_UPLOAD = 64 * 1024 * 1024


def number(value, label, low, high, integer=False):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        result = float("nan")
    if not low <= result <= high or (integer and not result.is_integer()):
        raise ValueError(f"{label}需填 {low}～{high} 之間的{'整數' if integer else '數字'}")
    return int(result) if integer else result


def optional_json(path):
    try:
        data = read_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def local_path(value, extension=None):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("請填入檔案或資料夾路徑")
    path = (ROOT / Path(value.strip().strip('"')).expanduser()).resolve()
    if extension and path.suffix.lower() != extension:
        raise ValueError(f"請使用 {extension} 檔案")
    return path


def intervals(data):
    start, end, test_start, test_end = (
        number(data.get(key), label, 0, 3600) for key, label in (
            ("cal_start", "校準開始"), ("cal_end", "校準結束"),
            ("test_start", "定位開始"), ("test_end", "定位結束")
        )
    )
    if end - start < 4:
        raise ValueError("校準區間至少需要 4 秒的固定寬頻聲")
    if test_start < end or test_end - test_start < .1:
        raise ValueError("定位區間需排在校準後，長度至少 0.1 秒")
    return start, end, test_start, test_end


def mic_count(value):
    return number(value, "麥克風數量", 3, 4, integer=True)


class Workbench:
    def __init__(self, scratch):
        self.scratch = scratch
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.RLock()
        self.engine = None
        self.devices = []
        self.files = {}
        self.jobs = {}
        self.active = None
        self.worker = None

    def bootstrap(self):
        config = optional_json(ROOT / "config" / "live.json")
        geometry = optional_json(ROOT / config.get("array_config", "config/array.json"))
        robot_base = "robot_base_positions_mm" in geometry
        database = ROOT / config.get("database", "data/speakers.json")
        tests = sorted(RUNS.glob("*_speaker_test/speakers.json"), reverse=True)
        if not database.is_file() and tests:
            database = tests[0]
        saved = optional_json(SETTINGS)
        speaker = dict(database=str(database), device="", mode="獨佔", duration=8, countdown=3,
                       name="", append_id="", append_database="",
                       gain=0, threshold=config.get("threshold", .65), margin=config.get("margin", .08))
        array = dict(count=config.get("microphone_count", 4), mode="獨佔",
                     rate=config.get("sample_rate_hz", 48000), duration=28, countdown=3,
                     cal_start=1, cal_end=9, test_start=15, test_end=23, expected="",
                     extrapolation=geometry.get("max_extrapolation_seconds", 30),
                     measured=geometry.get("geometry_measured", False),
                     coordinate_mode="robot_base_mm" if robot_base else "array_m",
                     positions=geometry.get("robot_base_positions_mm" if robot_base else "positions_m", []),
                     calibration_source=geometry.get("robot_base_calibration_source_mm" if robot_base
                                                     else "calibration_source_m"),
                     geometry_drafts={},
                     clock_jump_samples=geometry.get("clock_jump_samples", 0),
                     robot_frame=geometry.get("robot_frame"),
                     distances=config.get("calibration_distances_m", []),
                     devices=config.get("devices", []))
        for key, defaults in (("speaker", speaker), ("array", array)):
            previous = saved.get(key, {})
            if isinstance(previous, dict):
                if key == "array" and "coordinate_mode" not in previous:
                    if any(value not in (None, "") for point in previous.get("positions", [])
                           if isinstance(point, list) for value in point):
                        previous = dict(previous, coordinate_mode="array_m",
                                        calibration_source=previous.get("calibration_source_m"))
                defaults.update({key: previous[key] for key in defaults.keys() & previous.keys()})
        return dict(token=self.token, speaker=speaker, array=array,
                    page="array" if saved.get("page") == "array" else "speaker",
                    revision=saved.get("revision", 0))

    def save_settings(self, data):
        revision = data.get("revision", 0)
        with self.lock:
            previous = optional_json(SETTINGS)
            if revision >= previous.get("revision", 0):
                save_json(SETTINGS, dict(speaker=data["speaker"], array=data["array"],
                                        page=data.get("page", "speaker"), revision=revision))
        return dict(path=str(SETTINGS))

    def refresh_devices(self, reinitialize):
        with self.lock:
            if self.active:
                raise ValueError("請等待目前的測試完成，再重新讀取裝置")
            if reinitialize:
                # PortAudio caches the device list until it is initialized again.
                sd._terminate()
                sd._initialize()
            hosts = sd.query_hostapis()
            self.devices = [dict(index=index, name=device["name"], rate=device["default_samplerate"])
                            for index, device in enumerate(sd.query_devices())
                            if device["max_input_channels"] and
                            hosts[device["hostapi"]]["name"] == "Windows WASAPI"]
            return self.devices

    def speaker_engine(self):
        if self.engine is None:
            from src.speakers import SpeakerEngine
            self.engine = SpeakerEngine()
        return self.engine

    def roster(self, path, verify=False):
        path = local_path(path, ".json")
        if verify:
            if not path.is_file():
                raise ValueError("找不到這個聲紋資料庫，請確認路徑或按「建立空白資料庫」")
            with self.lock:
                saved = self.speaker_engine().database(path)
        else:
            saved = optional_json(path)
        speakers = saved.get("speakers", {})
        return dict(database=str(path), exists=path.is_file(),
                    speakers=[dict(id=name, recordings=row.get("recordings"))
                              for name, row in sorted(speakers.items()) if isinstance(row, dict)])

    def register_file(self, path, name=None):
        token = secrets.token_urlsafe(16)
        with self.lock:
            self.files[token] = path
        return dict(id=token, name=name or path.name, url=f"/media/{token}/{quote(path.name)}",
                    path=str(path))

    def get_file(self, file_id):
        with self.lock:
            path = self.files.get(file_id)
        if path is None:
            raise ValueError("找不到這份錄音，請重新錄製或選取音檔")
        return path

    def waveform(self, item):
        rate, audio = load_audio(self.get_file(item["id"]), 16000)
        starts = np.unique(np.linspace(0, len(audio) - 1, min(450, len(audio)), dtype=int))
        peaks = np.maximum.reduceat(np.abs(audio), starts)
        return dict(item, duration=len(audio) / rate, peaks=peaks.round(5).tolist())

    def import_wav(self, source, length, name, analyze):
        if not 44 <= length <= MAX_UPLOAD or not name.lower().endswith(".wav"):
            raise ValueError("請選擇 64 MB 以內的 WAV 檔")
        folder = Path(mkdtemp(prefix="import-", dir=self.scratch))
        path = folder / "audio.wav"
        item = None
        try:
            with path.open("wb") as output:
                remaining = length
                while remaining:
                    chunk = source.read(min(remaining, 64 * 1024))
                    if not chunk:
                        raise ValueError("音檔上傳中斷")
                    output.write(chunk)
                    remaining -= len(chunk)
            item = self.register_file(path, name)
            if analyze:
                item = self.waveform(item)
            return item
        except Exception:
            if item:
                with self.lock:
                    self.files.pop(item["id"], None)
            path.unlink(missing_ok=True)
            folder.rmdir()
            raise

    def tracks(self, folder, count):
        path = local_path(folder)
        return [self.register_file(track) for track in tracks_in(path, count)]

    def clip_args(self, data):
        path = self.get_file(data.get("id"))
        start = number(data.get("start"), "發話開始", 0, 3600)
        end = data.get("end")
        end = None if end in (None, "") else number(end, "發話結束", 0, 3600)
        gain = number(data.get("gain"), "數位增益", -24, 24)
        return path, start, end, gain

    def perform(self, action, data, stop, report):
        if action in ("record-speaker", "record-array"):
            return self.record(action == "record-array", data, stop, report)
        if action == "direction":
            if data.get("measured") is not True:
                raise ValueError("請量測並填入麥克風座標後，勾選「座標已量測」")
            count = mic_count(data.get("count"))
            coordinate_mode = data.get("coordinate_mode", "array_m")
            if coordinate_mode not in ("robot_base_mm", "array_m"):
                raise ValueError("請選擇座標格式")
            robot_base = coordinate_mode == "robot_base_mm"
            limit = 100_000 if robot_base else 100
            positions = [[number(value, f"{label} 的 {axis} 座標", -limit, limit)
                          for axis, value in zip("XYZ", pair)]
                         for label, pair in zip("ABCD"[:count], data["positions"])]
            source = data.get("calibration_source", data.get("calibration_source_m"))
            distances = None
            geometry = dict(geometry_measured=True,
                            clock_jump_samples=number(data.get("clock_jump_samples", 0),
                                                      "時間跳變取樣數", 0, 65536, integer=True),
                            max_extrapolation_seconds=number(data.get("extrapolation"), "外推上限", 0, 3600))
            if robot_base:
                geometry["robot_base_positions_mm"] = positions
            else:
                geometry.update(positions_m=positions, robot_frame=data.get("robot_frame"))
            if source is not None and any(value not in (None, "") for value in source):
                if len(source) != 3:
                    raise ValueError("請填入校準喇叭的 X、Y、Z 座標")
                source_key = "robot_base_calibration_source_mm" if robot_base else "calibration_source_m"
                geometry[source_key] = [number(value, f"校準喇叭的 {axis} 座標", -limit, limit)
                                        for axis, value in zip("XYZ", source)]
            else:
                distances = [number(distance, f"{label} 的校準聲程", .001, 100)
                             for label, distance in zip("ABCD"[:count], data["distances"])]
            expected = data.get("expected")
            expected = None if expected in (None, "") else number(expected, "真值角度", -180, 180)
            ids = data["tracks"][:count]
            if len(ids) != count or len(set(ids)) != count:
                raise ValueError(f"請為 A～{'ABCD'[count - 1]} 指定 {count} 軌不同的 WAV")
            paths = [self.get_file(file_id) for file_id in ids]
            if len(set(paths)) != count:
                raise ValueError("各軌須使用不同的音檔")
            spans = intervals(data)
            folder = paths[0].parent
            if paths != [folder / f"{label}.wav" for label in "ABCD"[:count]]:
                folder = Path(mkdtemp(prefix="array-", dir=self.scratch))
                for label, path in zip("ABCD", paths):
                    shutil.copyfile(path, folder / f"{label}.wav")
            result, clock, output = localize_run(folder, count, geometry, distances, *spans, expected)
            return dict(result=result, clock=clock, folder=str(output),
                        output=self.register_file(output / "direction.json"),
                        calibration=self.register_file(output / "clock.json"))
        database = local_path(data.get("database"), ".json")
        with self.lock:
            engine = self.speaker_engine()
        if action == "create":
            with database_lock(database):
                if database.exists():
                    raise ValueError("這個資料庫已存在，請改用「載入名單」或換檔名")
                save_json(database, engine.database(database))
        elif action == "rename":
            engine.rename(database, data["current"], data["updated"].strip())
        elif action in ("enroll", "append"):
            samples = [self.clip_args(sample) for sample in data["samples"]]
            append = action == "append"
            if len(samples) < (1 if append else 2):
                raise ValueError("請加入至少一段新錄音" if append else "請加入至少兩段不同的原始錄音")
            hashes = [fingerprint(sample[0]) for sample in samples]
            if len(set(hashes)) != len(samples):
                raise ValueError("請使用不同的原始錄音")
            from src.speakers import validate_user_id
            name = data["name"].strip()
            validate_user_id(name)
            store = engine.append_samples if append else engine.enroll
            with TemporaryDirectory(dir=self.scratch) as temp:
                clips = []
                for index, sample in enumerate(samples):
                    folder = Path(temp) / str(index)
                    folder.mkdir()
                    clips.append(speaker_clip(folder, *sample))
                store(name, clips, database, source_hashes=hashes)
        elif action == "identify":
            threshold = number(data.get("threshold"), "相似度門檻", -1, 1)
            margin = number(data.get("margin"), "最低分差", 0, 2)
            if not engine.database(database)["speakers"]:
                raise ValueError("聲紋資料庫沒有講者，請先註冊")
            with TemporaryDirectory(dir=self.scratch) as temp:
                result = engine.identify(speaker_clip(Path(temp), *self.clip_args(data["clip"])),
                                         database, threshold, margin)
            output = new_run("speaker") / "recognition.json"
            save_json(output, dict(database=str(database), margin=margin, **result))
            return dict(result=result, margin=margin, threshold=threshold, output=self.register_file(output))
        return self.roster(str(database))

    def record(self, array, data, stop, report):
        count = mic_count(data.get("count")) if array else 1
        indices = data.get("devices", [])[:count] if array else [data.get("device")]
        available = {device["index"] for device in self.devices}
        if len(indices) != count or any(index not in available for index in indices):
            raise ValueError("請為每個孔位選擇有效的麥克風")
        if len(set(indices)) != count:
            raise ValueError("每個孔位需選用不同的麥克風")
        seconds = number(data.get("duration"), "錄音長度", 5, 60, integer=True)
        countdown = number(data.get("countdown"), "倒數", 0, 10, integer=True)
        rate = None
        if array:
            rate = number(data.get("rate"), "取樣率", 8000, 192000, integer=True)
            cal_start, cal_end, test_start, test_end = intervals(data)
            if test_end + .3 > seconds:
                raise ValueError("錄音請至少比定位結束時間多 0.3 秒")
        for left in range(countdown, 0, -1):
            report(f"{left} 秒後開始錄音，請準備", 0)
            if stop.wait(1):
                raise InterruptedError("已停止錄音")

        def progress(elapsed):
            if not array:
                cue = "請說話"
            elif elapsed < cal_start:
                cue = "校準聲源先保持固定"
            elif elapsed < cal_end:
                cue = "校準段，請持續播放固定寬頻聲"
            elif elapsed < test_start:
                cue = "請移動到待測方向"
            elif elapsed < test_end:
                cue = "定位段，請播放待測聲音"
            else:
                cue = "測試段完成"
            report(f"錄音中 {elapsed:.1f} / {seconds} 秒 · {cue}", elapsed / seconds)

        folder = Path(mkdtemp(prefix="record-", dir=self.scratch))
        try:
            folder, summary = capture_to_directory(indices, seconds, data.get("mode") == "獨佔",
                                                   samplerate=rate, stop=stop, on_progress=progress,
                                                   on_started=lambda: progress(0), folder=folder)
        except Exception:
            shutil.rmtree(folder)
            raise
        files = self.tracks(str(folder), count) if array else [self.waveform(self.register_file(folder / "A.wav"))]
        return dict(folder=str(folder), files=files, summary=summary)

    def start_job(self, action, data):
        messages = {
            "record-speaker": "準備聲紋錄音", "record-array": "準備多軌錄音",
            "create": "建立聲紋資料庫", "rename": "更新講者 ID", "enroll": "處理註冊樣本",
            "append": "正在擴增聲紋樣本",
            "identify": "正在比對聲紋", "direction": "正在校準時間差並估算方位",
        }
        if action not in messages:
            raise ValueError("找不到測試操作")
        with self.lock:
            if self.active:
                raise ValueError("另一項測試正在進行，請等待完成")
            job_id = secrets.token_urlsafe(16)
            state = dict(status="running", message=messages[action], progress=None,
                         can_stop=action.startswith("record-"))
            stop = threading.Event()
            self.jobs[job_id] = state
            self.active = job_id
            self.stop = stop

        def report(message, progress):
            with self.lock:
                state.update(message=message, progress=progress)

        def run():
            try:
                result = self.perform(action, data, stop, report)
                final = dict(status="done", message="完成", result=result)
            except InterruptedError as error:
                final = dict(status="stopped", message=str(error))
            except Exception as error:
                final = dict(status="error", message=str(error))
            with self.lock:
                state.update(final, can_stop=False)
                self.active = None
                if len(self.jobs) > 30:
                    del self.jobs[next(iter(self.jobs))]

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()
        return dict(id=job_id)

    def job_state(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise ValueError("找不到這項測試")
            return dict(self.jobs[job_id])

    def cancel(self, job_id):
        with self.lock:
            if self.active == job_id and self.jobs[job_id]["can_stop"]:
                self.stop.set()
            return self.job_state(job_id)

    def close(self):
        with self.lock:
            if self.active:
                self.cancel(self.active)
        if self.worker is not None:
            self.worker.join()


class LocalServer(ThreadingHTTPServer):
    daemon_threads = False


class Handler(BaseHTTPRequestHandler):
    timeout = 10
    def log_message(self, *_):
        pass

    def send_data(self, body, content_type, status=HTTPStatus.OK):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, body, status=HTTPStatus.OK):
        self.send_data(json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf8"),
                       "application/json; charset=utf-8", status)

    def send_file(self, path, content_type):
        with path.open("rb") as source:
            size = path.stat().st_size
            start, end = 0, size - 1
            requested_range = self.headers.get("Range") if content_type == "audio/wav" else None
            if requested_range:
                match = re.fullmatch(r"bytes=(\d{1,18})-(\d{0,18})", requested_range)
                if match:
                    start = int(match[1])
                    end = min(end, int(match[2])) if match[2] else end
                if not match or end < start:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            self.send_response(HTTPStatus.PARTIAL_CONTENT if requested_range else HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_type.startswith("text/html"):
                self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            if content_type == "audio/wav":
                self.send_header("Accept-Ranges", "bytes")
            if requested_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = source.read(min(remaining, 64 * 1024))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def host_allowed(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        self.dispatch(self.get)

    def do_POST(self):
        self.dispatch(self.post)

    def dispatch(self, method):
        try:
            if not self.host_allowed():
                self.send_json({"error": "請透過程式顯示的本機網址開啟"}, HTTPStatus.FORBIDDEN)
                return
            method(unquote(urlsplit(self.path).path))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except FileNotFoundError:
            self.send_json({"error": "檔案不存在"}, HTTPStatus.NOT_FOUND)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)

    def get(self, path):
        app = self.server.app
        static = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                  "/style.css": ("style.css", "text/css")}
        if path in static:
            file, kind = static[path]
            self.send_file(WEB / file, kind + "; charset=utf-8")
        elif path in [f"/test-audio/{name}" for name in TEST_AUDIO]:
            self.send_file(ROOT / "assets" / "test_audio" / path.rsplit("/", 1)[1], "audio/wav")
        elif path.startswith("/media/"):
            file = app.get_file(path.split("/")[2])
            self.send_file(file, "audio/wav" if file.suffix == ".wav" else "application/json")
        elif path == "/api/bootstrap":
            self.send_json(app.bootstrap())
        else:
            self.send_json({"error": "找不到頁面"}, HTTPStatus.NOT_FOUND)

    def post(self, path):
        app = self.server.app
        origin = self.headers.get("Origin")
        token = self.headers.get("X-Workbench-Token", "")
        if (origin is not None and origin != f"http://{self.headers['Host']}") or not secrets.compare_digest(token, app.token):
            self.send_json({"error": "請重新開啟測試介面"}, HTTPStatus.FORBIDDEN)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if path in ("/api/import-speaker", "/api/import-track"):
            self.send_json(app.import_wav(self.rfile, length, unquote(self.headers.get("X-File-Name", "")),
                                          path == "/api/import-speaker"))
            return
        if not 0 <= length <= 128 * 1024:
            raise ValueError("設定資料太大")
        data = json.loads(self.rfile.read(length)) if length else {}
        if not isinstance(data, dict):
            raise ValueError("無效的設定格式")
        if path == "/api/devices":
            result = app.refresh_devices(data.get("refresh", False))
        elif path == "/api/roster":
            result = app.roster(data.get("database"), data.get("verify", False))
        elif path == "/api/settings":
            result = app.save_settings(data)
        elif path == "/api/tracks":
            result = app.tracks(data.get("folder"), mic_count(data.get("count")))
        elif path == "/api/start":
            result = app.start_job(data["action"], data["settings"])
        elif path == "/api/status":
            result = app.job_state(data["id"])
        elif path == "/api/stop":
            result = app.cancel(data["id"])
        else:
            self.send_json({"error": "找不到操作"}, HTTPStatus.NOT_FOUND)
            return
        self.send_json(result)


def main(open_browser=True):
    with TemporaryDirectory(prefix="voice-detect-") as scratch, LocalServer(("127.0.0.1", 0), Handler) as server:
        server.app = Workbench(Path(scratch))
        address = f"http://127.0.0.1:{server.server_port}"
        print(f"麥克風測試台 {address}", flush=True)
        print("關閉此視窗或按 Ctrl+C 結束。", flush=True)
        if open_browser:
            webbrowser.open(address)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            # Drain requests before removing the temporary WAVs.
            server.server_close()
            server.app.close()
