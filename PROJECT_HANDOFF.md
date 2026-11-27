# 專案交接與目前狀態

更新：2026-11-27。操作者為王翊丞。儲存庫：https://github.com/wdps100397-lang/voice-detect 。本機工作目錄為 `C:\Users\王翊丞\Desktop\TMrobot\voice_detect`。

## 專案目的與界線

使用三／四支 USB 麥克風，估計連續的三維聲源方向，並以 A 軌做聲紋辨識。目標是讓 TM5-900 手臂在固定高度下依水平角度轉向講話者，不是只辨識幾個預設點。方向估計不是聲源 XYZ 定位，也不是手臂關節控制器。手臂目前未連接。

Windows 負責錄音與推論，WSL ROS 2 節點提供 recognition、direction、identity。Project A 的 LLM 只讀 identity。五欄位為 user_id、location、azimuth_deg、timestamp、source；未知 ID 為空字串、無有效角度為 null、source=voice_detect。接收端是否接受此來源及空角度仍需現場驗收，舊上游檢查不能代表目前版本。

## 目前實體配置

以下是 11/25 測試快照，不是重新插拔後的保證；先執行 `voice.cmd devices` 核對。

| 孔位 | 實體位置 | WASAPI 編號 | 相對座標 m | USB 路徑 |
| --- | --- | --- | --- | --- |
| A | 右前 | 36 | [0.1555,0.11,0] | 直接插電腦 |
| B | 左前 | 35 | [-0.1555,0.11,0] | Hub |
| C | 左後 | 33 | [-0.1555,-0.11,0] | Hub |
| D | 右後 | 30 | [0.1555,-0.11,0] | Hub |

X 朝右、Y 朝前、Z 朝上。前 0°、左 +90°、右 -90°。四支同屬 Intel USB 3.10 控制器；電腦另有控制器，但實體孔位與分流效果未驗證。型號為 MI-305，裝置名稱 USBAudio2.0 不代表目前載入 usbaudio2.sys；當次查到服務為 usbaudio。

校準手機出聲孔為 `[0.155,0.310,0]` m，名義角度 -26.565°。`config/array.json` 目前是這組桌面相對座標、geometry_measured=true、clock_jump_samples=256，尚未換成工作台正式量測。將來以校正 TCP 在 RobotBase 讀各孔及喇叭的 XYZ，填 robot_base_positions_mm／robot_base_calibration_source_mm。RobotBase 模式零度為 +X、+90° 為 +Y，不能混用桌面角度。TMflow TCP、安裝偏心與手臂原點關係仍需部署驗證。

## 已完成與尚未完成

- 11/27 執行 `.venv-audio/Scripts/python.exe voice_app.py test`，49 項通過，屬軟體驗證。
- 3D GCC-PHAT／SRP 搜尋、三／四支切換、獨立功能開關、錯誤隔離、CLI、網頁及 ROS 套件已提供。平面陣列仍有仰角鏡像歧義，接近正上方時水平角難判定。0.1° 是輸出解析度。
- 已有少量真人聲紋註冊與新錄音辨識成功，尚無正式辨識率。啟動 live 的 data/speakers.json 目前不一定存在；歷史聲紋資料在 runs 下，使用前核對 user_id 和模型雜湊。
- 四支固定聲源的時間穩定性未通過。手臂控制、真實 ROS DDS、Windows／WSL 全流程及 LLM 整合未驗收。
- B/C/D 歷史 AGC 讀回關閉，A 缺相同 AGC 控制介面。configure 對每支要求恰一個 AGC，可能拒絕此組設備。不可宣稱四支都已由 configure 成功設定。

## 同步問題的實測證據

`runs/20261125_182914_native_packet_check/test_result.json`：40 秒固定聲源，已知沙沙聲在 78 個檢查窗中有 77 個被至少三軌確認。約 17.25 秒開始，B/C/D 相對 A 同時多約 256 點，即 5.33 ms。使用直接 WASAPI 擷取仍可重現；裝置位置連續、中斷旗標為零。

9～15 秒校準後再做 256 點補償，47 個後續窗中 46 個被程式接受，但角度仍在 -41.8°～-18.8°，相對名義校準方向最大偏差約 15.23°。這表明現有通過條件仍可能接受偏角結果。固定聲源檢查只能驗證時間穩定性，不能證明未知方向精度。

原生 packet 的時間標記不是已驗證的 ADC 同步時鐘；讀得到時間與沒有中斷旗標，都不能證明同步。USB 頻寬競爭、裝置實際取樣速度、驅動或韌體緩衝處理是待區分假設，尚未確定是 Hub 塞車，也尚未證明必須換硬體。

## 新的通訊與錄音比較方案

`src/audio_transport.py` 提供 exclusive、shared、shared_raw。shared_raw 以原生 WASAPI、RAW 選項、約 100 ms 緩衝及 MMCSS 錄音執行緒擷取，保存 device position／QPC／旗標。RAW 仍可能保留驅動、硬體及端點固定處理。

診斷程式已讓四支 shared_raw 開啟兩秒成功：`runs/20261125_184101_native_packet_check_shared_raw_4ch/`。這不是長時間聲學驗證。正式 CLI／live 新路徑尚未做完整硬體驗證。原生獨佔格式檢查只接受單聲道 16-bit、48 kHz，不能用程式內重取樣宣稱降低了 USB 傳輸量。

- CLI：`voice.cmd record --devices 36 35 33 30 --seconds 40 --raw-shared --capture-latency 0.1`。
- live：JSON 可加 capture_mode=shared_raw、capture_latency_seconds=0.1；主設定尚未切換，仍預設獨佔。
- record 新增 timing.json；LiveCapture 保留有界時間標記。device position 缺段或非起始中斷會回報錯誤。
- 網頁的共享選項仍為一般共享，尚無 RAW 選項。使用者目前要求直接控制，不要操作網頁。

使用者最後回覆「現在不方便播放」，後續沒有聲源已準備好的授權資訊。方便時先確認手機在校準位置持續播放，再執行：

    .venv-audio/Scripts/python.exe runs/communication_comparison.py

它依序錄單支 A 獨佔、四支共享 RAW、四支 250 ms 緩衝獨佔各 40 秒，之後以已知波形分析。裝置索引和聲源狀態須先核對。此比較尚未執行，不得描述為已成功改善。若仍失敗，再做不同 USB 控制器分流與硬體／驅動隔離測試，一次只改一項條件。

## 操作與溝通要求

稱呼王翊丞，使用繁體中文、說人話，一次給一個實體動作。先等使用者明確聽到沙沙聲的回覆才錄，不可把「開始測試」自行當作已播放。附帶 broadband_30s.wav 前後各 5 秒安靜，中間才是 20 秒噪音。不要切短靜音後直接拼接再算時間差。

校準與測方向須保持同一組串流開啟；重開串流就重新校準。校準完成再通知移動，未知方位先盲判再問真值。不要用已知待測角度反推校準，或將軟體測試、零旗標、低校準殘差宣稱為實體精度成功。

## 資料與 GitHub

runs、data 已納入版本控制，WAV 使用 Git LFS。取回音檔用 `git lfs install`、`git lfs pull`。11/27 快照含 114 個音檔，包含中斷或未播放的失敗場次，不可全部視為有效資料。歷史 RESULT.md 保留當時結果，現況註記優先用於理解目前狀態。虛擬環境、快取、密鑰和執行中 lock 不上傳。

工作環境使用 `.venv-audio`（Python 3.11）；另一個 `.venv` 缺少部分依賴。既有修改與歷史原音必須保留，不能為修復同步而覆寫或刪除。
