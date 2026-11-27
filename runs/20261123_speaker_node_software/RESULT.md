# 真實 speaker_identity 節點軟體驗證

> 現況註記（2026-11-27）：以下保留本場次的歷史結果、測試數與裝置編號，不代表目前部署狀態。最新軟體回歸為 49 項通過；四支桌面錄音已確認時間跳變，256 點補償仍未達到可靠角度精度，共享 RAW 長時間聲學驗證及 ROS／LLM 整合尚待完成。最新設定與證據請看根目錄 PROJECT_HANDOFF.md 和 runs/README.md。


參考 GitHub feature/live-dialogue-monitor commit 93d9bc1af753deadd4ab2c5487438e2cc765301c，參考倉庫保持乾淨。

- 新增持續 WASAPI 三軌收音、定長緩衝、啟動時鐘校準、滾動音窗校正、既有聲紋模型與方位估計。
- 新增 ROS 2 套件 voice_detect_node，speaker_identity 節點管理 Windows worker，發布 /project_a/speaker/identity。
- Topic 使用原生五欄位、reliable／volatile／depth 10、0.5 秒更新，保留觀測時間。安靜及過期資料不會刷新身分。
- voice.cmd test 二十項全部通過，包含合成方位、時鐘校正與 ROS 觀測暫存流程。
- ROS package XML、Python 語法與 WSL 啟動腳本 bash 語法檢查通過。

本輪依使用者要求以軟體與假資料測試，實體音訊辨識與 DDS 傳輸未驗收。本機尚需填入實際幾何、三支裝置編號和真人註冊資料；ROS 環境需安裝 rclpy。最新接收端仍只接受 source=mock，真實來源 voice_detect 的接收支援需由對接端處理。
