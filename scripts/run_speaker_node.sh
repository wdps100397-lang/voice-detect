#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$project_dir/runs/ros_install/setup.bash" ]]; then
  set +u
  source "$project_dir/runs/ros_install/setup.bash"
  set -u
fi
command -v ros2 >/dev/null
python_exe="$project_dir/.venv-audio/Scripts/python.exe"
worker_path="$(wslpath -w "$project_dir/voice_app.py")"
config_path="$(wslpath -w "$project_dir/config/live.json")"
worker_command="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$python_exe" -X utf8 -u "$worker_path" live --config "$config_path")"
exec ros2 run voice_detect_node speaker_identity --ros-args -p "worker_command:=$worker_command" "$@"
