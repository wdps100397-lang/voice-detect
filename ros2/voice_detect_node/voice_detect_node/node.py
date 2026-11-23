"""ROS node owning the Windows capture/inference worker process."""
import json
import math
import subprocess
import threading
import time

TOPIC = "/project_a/speaker/identity"
RECOGNITION_TOPIC = "/project_a/speaker/recognition"
DIRECTION_TOPIC = "/project_a/speaker/direction"


class IdentityMailbox:
    def __init__(self):
        self.latest = None
        self.lock = threading.Lock()

    def update(self, data):
        if not isinstance(data, dict) or set(data) != {"user_id", "location", "azimuth_deg", "timestamp", "source"}:
            raise ValueError("Invalid worker observation fields")
        if data["source"] != "voice_detect":
            raise ValueError("Expected real voice_detect observation")
        for key, limit in (("user_id", 64), ("location", 128)):
            if not isinstance(data[key], str) or len(data[key]) > limit:
                raise ValueError("Invalid " + key)
        for key, low, high in (("azimuth_deg", -180, 180), ("timestamp", 0, float("inf"))):
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError("Invalid " + key)
        with self.lock:
            if self.latest is None or data["timestamp"] >= self.latest["timestamp"]:
                self.latest = dict(data)

    def snapshot(self, now):
        with self.lock:
            if self.latest is None or not 0 <= now - self.latest["timestamp"] <= 3:
                return None
            return dict(self.latest)

    def clear(self):
        with self.lock:
            self.latest = None


class FeatureMailbox:
    def __init__(self):
        self.latest = None
        self.lock = threading.Lock()

    def update(self, data):
        timestamp = data.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Invalid feature timestamp")
        if data.get("source") != "voice_detect":
            raise ValueError("Invalid feature source")
        recognition = dict(data["recognition"])
        direction = dict(data["direction"])
        if recognition.get("status") not in ("recognized", "unknown", "unusable_audio", "disabled", "error"):
            raise ValueError("Invalid recognition status")
        if direction.get("status") not in ("ok", "ambiguous", "no_signal", "disabled", "error"):
            raise ValueError("Invalid direction status")
        user_id = recognition.get("user_id")
        if not isinstance(user_id, str) or len(user_id) > 64:
            raise ValueError("Invalid recognition user_id")
        if recognition["status"] != "recognized":
            recognition["user_id"] = ""
        angle = direction.get("azimuth_deg")
        if direction["status"] == "ok":
            if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle) or not -180 <= angle <= 180:
                raise ValueError("Invalid direction angle")
        else:
            direction["azimuth_deg"] = None
        value = dict(timestamp=timestamp, source="voice_detect",
                     recognition=recognition, direction=direction)
        with self.lock:
            if self.latest is None or timestamp >= self.latest["timestamp"]:
                self.latest = value
                return True
        return False

    def snapshot(self, now):
        with self.lock:
            if self.latest is None or not 0 <= now - self.latest["timestamp"] <= 3:
                return None
            return json.loads(json.dumps(self.latest))


def main(args=None):
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from std_msgs.msg import String

    class SpeakerIdentityNode(Node):
        def __init__(self):
            super().__init__("speaker_identity")
            self.mailbox = IdentityMailbox()
            self.features = FeatureMailbox()
            self.failure = None
            self.process = None
            command = self.declare_parameter("worker_command", [""]).value
            if not command or any(not isinstance(arg, str) or not arg for arg in command):
                raise ValueError("Set worker_command to the Windows Python executable and voice_app.py live arguments")
            if "mock_speaker_identity" in self.get_node_names():
                raise RuntimeError("Stop mock_speaker_identity before starting the real producer")
            qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.VOLATILE)
            self.publisher = self.create_publisher(String, TOPIC, qos)
            self.recognition_publisher = self.create_publisher(String, RECOGNITION_TOPIC, qos)
            self.direction_publisher = self.create_publisher(String, DIRECTION_TOPIC, qos)
            try:
                self.process = subprocess.Popen(list(command), stdout=subprocess.PIPE,
                                                text=True, encoding="utf8", errors="strict", bufsize=1)
                self.reader = threading.Thread(target=self.read_worker, daemon=True)
                self.reader.start()
                self.timer = self.create_timer(.5, self.publish_latest)
            except BaseException:
                self.close()
                raise

        def read_worker(self):
            try:
                for line in self.process.stdout:
                    event = json.loads(line)
                    if event.get("kind") == "identity":
                        self.mailbox.update(event["data"])
                    elif event.get("kind") == "features":
                        data = event["data"]
                        if self.features.update(data):
                            if data["direction"]["status"] != "ok" or data["recognition"]["status"] not in ("recognized", "unknown", "disabled"):
                                self.mailbox.clear()
                    elif event.get("kind") == "error":
                        raise RuntimeError(event.get("error", "Worker failed"))
                    elif event.get("kind") == "status":
                        self.get_logger().info("Capture worker: " + event["phase"])
                self.failure = "Capture worker exited"
            except Exception as error:
                self.failure = str(error)

        def publish_latest(self):
            if self.failure:
                self.get_logger().error(self.failure)
                rclpy.try_shutdown()
                return
            data = self.mailbox.snapshot(time.time())
            if data is not None:
                self.publisher.publish(String(data=json.dumps(data, ensure_ascii=False, allow_nan=False)))
            features = self.features.snapshot(time.time())
            if features is not None:
                for key, publisher in (("recognition", self.recognition_publisher), ("direction", self.direction_publisher)):
                    value = dict(features[key], timestamp=features["timestamp"], source=features["source"])
                    publisher.publish(String(data=json.dumps(value, ensure_ascii=False, allow_nan=False)))

        def close(self):
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait()
                if self.process.stdout:
                    self.process.stdout.close()
                if hasattr(self, "reader") and self.reader.is_alive():
                    self.reader.join(timeout=2)
            self.destroy_node()

    rclpy.init(args=args)
    node = None
    failed = False
    try:
        node = SpeakerIdentityNode()
        rclpy.spin(node)
        failed = bool(node.failure)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.close()
        rclpy.try_shutdown()
    if failed:
        raise SystemExit(1)
