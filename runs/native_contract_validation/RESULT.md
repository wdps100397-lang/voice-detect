# Project A 原生格式核對

2026-11-23 使用既有合成三軌，從 voice.cmd analyze 正式入口產生 analysis.json、identity.json、input.json。外部 STT 文字為手動建立的測試字串，timestamp=1000 為歷史測試時間，整輪未錄製真人語音。

- 定位直接輸出 azimuth_deg，已將基礎演算法改為前方 0、左正右負。
- 聲紋輸出 user_id，未知或不可用時為空字串。
- 事件與 STT 使用同一 utterance_id；不一致時拒絕合併。
- 直接輸出 speaker/identity 五個欄位與 agent/input 三個欄位。
- 十四項回歸與原生事件測試通過，套件依賴檢查通過。
- 唯讀載入 Project A commit 41003af729f6e755796cf1092086e33bc72b0b4e 的 validate_identity 核對。真實來源 voice_detect 被明確拒絕，錯誤為 only mock identity is supported in this prototype。僅在記憶體測試副本把 source 設為 mock 時，其餘欄位通過該驗證器。
- 使用者提供的兩份 NODE 文件雜湊均與該分支版本一致，參考倉庫 git status 保持乾淨。

未向 ROS、HTTP、LLM 或手臂端傳送資料。實際 ROS 傳輸、3 秒時效、連續更新、音訊片段對齊和真實來源支援仍待整合。
