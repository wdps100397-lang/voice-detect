"""ROS node owning the Windows capture/inference worker process."""
import json
import math
import subprocess
import threading
import time

TOPIC = "/project_a/speaker/identity"
RECOGNITION_TOPIC = "/project_a/speaker/recognition"
DIRECTION_TOPIC = "/project_a/speaker/direction"


class FeatureMailbox:
    def __init__(self):
        self.latest = None
        self.last_recognized = None
        self.lock = threading.Lock()

    def update(self, data):
        timestamp = data.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Invalid feature timestamp")
        if data.get("source") != "voice_detect":
            raise ValueError("Invalid feature source")
        recognition = dict(data["recognition"])
        direction = dict(data["direction"])
        status = recognition.get("status")
        if status not in ("recognized", "unknown", "pending", "insufficient_speech", "unusable_audio",
                          "disabled", "error"):
            raise ValueError("Invalid recognition status")
        if direction.get("status") not in ("ok", "ambiguous", "no_signal", "disabled", "error"):
            raise ValueError("Invalid direction status")
        user_id = recognition.get("user_id")
        if not isinstance(user_id, str) or len(user_id) > 64:
            raise ValueError("Invalid recognition user_id")
        if status == "recognized":
            if not user_id or user_id != user_id.strip() or user_id == "UNKNOWN":
                raise ValueError("Recognized speakers must have a valid user ID")
        else:
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
            if self.latest is not None and timestamp < self.latest["timestamp"]:
                return False
            if status == "recognized":
                self.last_recognized = user_id, timestamp
            elif status == "insufficient_speech" and self.last_recognized is not None:
                confirmed_id, confirmed_at = self.last_recognized
                recognition.update(status="held", user_id=confirmed_id, last_recognized_at=confirmed_at)
            else:
                self.last_recognized = None
            self.latest = value
            return True

    def snapshot(self, now):
        with self.lock:
            if self.latest is None or not 0 <= now - self.latest["timestamp"] <= 3:
                return None
            return json.loads(json.dumps(self.latest))


def topic_messages(features):
    """Build all publications from the same observation."""
    recognition = features["recognition"]
    direction = features["direction"]
    timestamp, source = features["timestamp"], features["source"]
    status = recognition["status"]
    recognition_time = recognition["last_recognized_at"] if status == "held" else timestamp
    messages = {
        RECOGNITION_TOPIC: dict(recognition, timestamp=recognition_time, source=source),
        DIRECTION_TOPIC: dict(direction, timestamp=timestamp, source=source),
    }
    if direction["status"] == "ok" and status in ("recognized", "unknown", "disabled"):
        messages[TOPIC] = dict(user_id=recognition["user_id"], location="",
                               azimuth_deg=direction["azimuth_deg"], timestamp=timestamp, source=source)
    return messages


def main(args=None):
    import rclpy
    from rclpy.node import Node
    from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
    from std_msgs.msg import String

    class SpeakerIdentityNode(Node):
        def __init__(self):
            super().__init__("speaker_identity")
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
            self.publishers_by_topic = {topic: self.create_publisher(String, topic, qos)
                                        for topic in (TOPIC, RECOGNITION_TOPIC, DIRECTION_TOPIC)}
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
                    if event.get("kind") == "features":
                        self.features.update(event["data"])
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
            features = self.features.snapshot(time.time())
            if features is not None:
                for topic, payload in topic_messages(features).items():
                    self.publishers_by_topic[topic].publish(
                        String(data=json.dumps(payload, ensure_ascii=False, allow_nan=False)))

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
