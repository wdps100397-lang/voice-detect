# 四支 USB 麥克風定位與聲紋辨識（可切換三支）

更新日期為 2026-11-23。

此專案提供聲紋辨識、水平聲源方向估計，以及 Project A 的 ROS 2 發布節點。麥克風數量可設定為三支或四支，聲紋與定位各自可開關。Windows 負責 USB 收音與模型推論，WSL 的 ROS 節點管理辨識程序並發布結果。外部 STT 負責產生文字。

## 目前狀態

| 項目 | 實際狀態 |
| --- | --- |
| 三支／四支處理 | 收音、時間校準、方向估計與 CLI 已支援，三支及四支合成資料測試通過。 |
| 獨立功能開關 | 兩項全開、只開聲紋、只開定位、兩項全關均通過軟體測試。 |
| 功能錯誤隔離 | 模型初始化、幾何設定、校準、推論及對齊失敗的測試，確認其他可用功能可繼續輸出。 |
| 三支實體 MI-305 | 2026-11-22 完成一分鐘同錄與手機測試聲對照，三軌收到樣本，串流未回報溢位或中斷。 |
| 四支實體麥克風 | 尚待驗證。 |
| 實體方向精度、長時間漂移 | 尚待量測，合成測試的角度結果屬於軟體驗證。 |
| 真人聲紋 | 尚待註冊及盲測，0.65／0.08 為暫定門檻。 |
| ROS 發布節點 | 程式及套件已提供，觀測暫存與過期邏輯通過軟體測試，實際 DDS 傳輸與 Windows／WSL 程序聯動尚待部署驗收。 |
| Project A 接收端 | 已核對的 commit 93d9bc1 只接受 source=mock，真實來源支援仍待接收端處理。 |
| 自動測試 | 35 項通過，包含 CLI、模型推論、三／四支校準、功能開關及故障隔離。 |

附帶設定預設四支、兩功能全開。devices 為空，geometry_measured 為 false，四個座標採邊長 20 公分正方形範例，校準距離也需以實測替換。部署時依啟用功能填寫裝置、幾何與真人註冊資料。

## 取得專案與安裝

Repository 為私有，協作者取得權限後可複製。先安裝 Python 3.11 x64，包含 Python Launcher，再從 Windows 命令提示字元執行。

    git clone https://github.com/wdps100397-lang/voice-detect.git
    cd voice-detect
    setup.cmd
    voice.cmd test

PowerShell 中使用 .\setup.cmd 與 .\voice.cmd test。setup.cmd 建立 .venv-audio、安裝固定版本依賴並驗證模型雜湊。Repository 已包含模型檔案，檔案缺少或雜湊不符時會重新下載。

程式的預設路徑以專案位置計算。換電腦時重新建立 Python 環境，重新確認裝置編號與實體 A/B/C/D 對應。runs 保存本機錄音及結果，data 保存真人註冊資料，兩者由各使用環境自行保存。

## 專案結構

    voice-detect/
      voice.cmd                  Windows 操作入口
      voice_app.py               CLI 與功能整合
      setup.cmd                  安裝 Python 環境與驗證模型
      requirements.txt           固定版本依賴
      src/
        capture.py               一至四個輸入的分軌錄音
        hardware.py              三／四個 USB 輸入的 AGC 與增益設定
        localization.py          三／四軌時間校準與左正右負方位
        speakers.py              以 Project A user_id 註冊與辨識
        features.py              功能開關、推論錯誤隔離與合併輸出條件
        live.py                  持續收音、滾動音窗與功能狀態
        events.py                離線事件、外部 STT 配對與原生 JSON
        common.py                音訊、JSON、檔案鎖與雜湊
      config/                    即時參數與幾何範例
      ros2/voice_detect_node/     speaker_identity ROS 2 套件
      scripts/                   模型下載與 WSL 啟動腳本
      models/                    WeSpeaker 模型與來源紀錄
      assets/test_audio/         沙沙聲與掃頻測試音檔
      schemas/                   agent/input 與合併身分 JSON 欄位契約
      tests/                     軟體回歸測試
      MI-305_CAD/                麥克風與支架 CAD
      TEST_PLAN.md               已測結果與後續驗收項目

mic_check.py 和 audio_controls.py 保留為早期操作入口。

## 麥克風數量與功能開關

在 [config/live.json](config/live.json) 修改以下參數，重新啟動節點後生效。

    {
      "microphone_count": 4,
      "enable_speaker_recognition": true,
      "enable_localization": true
    }

microphone_count 接受整數 3 或 4，省略時也預設四支。若啟用定位，程式採用 devices、positions_m、calibration_distances_m 的前 N 筆。先填好四支的實際資料後，往後只改 microphone_count 即可切換。

| 聲紋 | 定位 | 運行方式 |
| --- | --- | --- |
| true | true | 以 N 支收音與校準，A 軌做聲紋，所有有效定位軌計算方向。 |
| true | false | 使用 devices 第一個輸入 A，跳過幾何檔案、定位計算及時鐘校準。 |
| false | true | 使用 N 支收音與校準，跳過聲紋模型、資料庫與註冊檢查。 |
| false | false | 保持節點待命，定期輸出兩項 disabled 狀態，收音與模型保持關閉。 |

初始化或推論錯誤記為對應功能的 error，其他可用功能繼續運作。初始化設定修正後重啟節點可重新載入。若共享收音發生設備斷線或串流溢位，worker 會結束並提供錯誤訊息，ROS 節點也會停止，需要修正設備後重新啟動。

### 其他即時設定

| 欄位 | 預設值與用途 |
| --- | --- |
| devices | 空陣列，部署時填入 WASAPI 編號，依實體 A/B/C/D 排序。 |
| sample_rate_hz | 48000，啟用的輸入需能開啟此格式。 |
| array_config | config/array.json，定位用幾何檔。 |
| database | data/speakers.json，聲紋註冊資料庫。 |
| window_seconds | 2.5，每輪處理最近一個音窗。 |
| update_seconds | 0.5，worker 目標更新間隔，實際速度取決於推論時間。 |
| calibration_seconds | 8，定位啟動校準長度，可設 5～30 秒。 |
| calibration_distances_m | 四個範例 1.0，需換成校準聲源至各孔的實際公尺距離。 |
| max_clock_age_seconds | 0，持續使用當次時鐘模型。設正值後，過期定位會回報 error，聲紋可繼續。 |
| threshold／margin | 0.65／0.08，聲紋最高分與第二名差距的暫定門檻。 |

max_clock_age_seconds=0 代表停用時間上限檢查。長時間時鐘模型的精度仍待實測。

## Windows 收音與聲紋準備

列出裝置，選擇 Windows WASAPI 下的實體 USB 麥克風。編號可能在重新插拔或換電腦後改變，同一支也可能出現在不同音訊介面下。

    .\voice.cmd devices

三支或四支 USBAudio2.0 都連接時，可統一設定增益並關閉 AGC。

    .\voice.cmd configure

configure 處理當時偵測到的三個或四個 USBAudio2.0 輸入，固定增益採第一個端點的現有值，設定前後紀錄存入 runs。增益一致後，實體靈敏度差異仍需量測。

聲紋註冊使用工作台 users 的實際 ID。至少兩個不同的單聲道錄音檔，每檔需有至少兩秒被 VAD 偵測到的語音。

    .\voice.cmd enroll --user-id U_A --files sample1.wav sample2.wav
    .\voice.cmd identify --file query.wav

sample1.wav、sample2.wav、query.wav 需由使用環境準備。建議每段 5～10 秒、不同句子。模型採 WeSpeaker VoxCeleb ResNet34，輸入重取樣成 16 kHz，使用 WebRTC VAD 與 80 維 filterbank。辨識結果為 recognized、unknown 或 unusable_audio。未知身分的 user_id 為空字串，分數採餘弦相似度。

## 方向幾何與啟動校準

在 [config/array.json](config/array.json) 填入使用中的收音孔座標，單位公尺，x 朝右、y 朝前。三支或四支需採非共線且位置相異的配置，完成量測後設 geometry_measured=true。第四個附帶座標也是範例。

azimuth_deg 使用前方 0°、左側 +90°、右側 -90°、範圍 [-180,180]。方向模型採單聲源、水平遠場假設，距離應大於陣列尺寸數倍。

定位啟動時，保持校準聲源與麥克風靜止，讓各支持續收到同一個寬頻訊號。填入聲源至各孔距離，啟動後等待 phase=ready，再停止校準聲。每次重開 USB 串流重新估計初始偏移與時鐘漂移。校準段殘差過大會回報該功能錯誤。

定位使用所有麥克風配對的 GCC-PHAT 相關值，三支有三對、四支有六對。分析頻帶為 150～8000 Hz，回報 ok、ambiguous 或 no_signal。peak_margin 是峰值差距指標。硬體方位誤差與持續校準有效時間仍待量測。

## 持續節點啟動

Windows 可先啟動 worker。

    .\voice.cmd live --config config/live.json

stdout 為 UTF-8 JSON Lines。

| kind | 內容 |
| --- | --- |
| status | 初始化及狀態轉換，例如 calibrating、ready、observed、degraded。 |
| features | 各功能的完整狀態、數值或錯誤，附原始觀測 timestamp。 |
| identity | 符合合併條件的五欄位資料。 |
| error | worker 停止原因。 |

ROS 套件在 WSL 執行並管理 Windows worker。需具備 ROS 2 Humble、rclpy、std_msgs 和 colcon。從此專案在 WSL 的實際路徑執行以下命令。

    source /opt/ros/humble/setup.bash
    colcon build --base-paths ros2 --build-base runs/ros_build --install-base runs/ros_install
    source runs/ros_install/setup.bash
    bash scripts/run_speaker_node.sh

啟動腳本自動取得專案位置與 Windows Python 路徑。ROS_DOMAIN_ID 與 Project A 其他節點一致。先停止 mock_speaker_identity，讓真實發布端單獨提供觀測。

## ROS 輸出與接收端限制

三個 Topic 均使用 std_msgs/msg/String，data 為 JSON 字串，QoS 為 reliable／volatile／depth 10。ROS 計時器每半秒發布仍在三秒有效期內的最新資料，timestamp 保留音訊觀測時間。

| Topic | 資料與條件 |
| --- | --- |
| /project_a/speaker/recognition | status、user_id、可用的分數或 reason，以及 timestamp、source。單獨聲紋模式也會提供資料。 |
| /project_a/speaker/direction | status、azimuth_deg、可用的品質或 reason，以及 timestamp、source。無有效方向時角度為 null。 |
| /project_a/speaker/identity | user_id、location、azimuth_deg、timestamp、source。方向需為 ok，聲紋需為 recognized、unknown 或 disabled。 |

聲紋 disabled、error 或 unusable_audio 時，獨立 Topic 的 user_id 為空。定位 disabled、error、ambiguous 或 no_signal 時，獨立 Topic 的 azimuth_deg 為 null。接收端依 status 與 timestamp 判斷可用性。

只開定位時，合併 Topic 可發布空 user_id 與有效角度。只開聲紋時，身分從 recognition Topic 取得。兩項關閉時，獨立 Topic 會持續回報 disabled，舊合併觀測依三秒時效過期。

已核對 Project A feature/live-dialogue-monitor 的 commit 93d9bc1af753deadd4ab2c5487438e2cc765301c，當時的接收器有以下限制。

- speaker/identity 僅接受 source=mock，本專案真實來源為 voice_detect，接收端需擴充來源支援。
- Chat 當時僅訂閱合併 identity Topic。只開聲紋時若要使用 user_id，需接收 recognition Topic。
- use_speaker_identity=true 時，以文字提交當下三秒內最新觀測決定當輪身分，尚需整合音訊片段精確配對。
- direction 是相對麥克風的角度，location 保留空字串，交付區與手臂座標由後續系統定義。

接收器的後續更新需以其程式為準。參考來源見 [Project A 身分驗證程式](https://github.com/Ktliu-Tyler/TM-LLM-Robot/blob/93d9bc1af753deadd4ab2c5487438e2cc765301c/src/project_a_llm/project_a_llm/speaker_identity.py)。

## 離線錄音與分析

record 支援一至四個輸入，長度 5～60 秒。devices 的順序決定 A/B/C/D，實際檔案數與輸入數相同。

    .\voice.cmd record --devices 28 29 33 --seconds 30

上列編號僅為三支操作範例，以當次 devices 清單替換。預設使用 WASAPI 獨佔模式，--shared 可供對照。每輪建立 runs 子資料夾，保存各軌 WAV 和 summary.json。summary 包含輸入數量、格式、裝置、開始時間、模式和串流旗標。

calibrate、locate、analyze 接受三個或四個同取樣率、單聲道 WAV。校準聲程需逐檔對應。以下為三支範例，假定 1～10 秒為固定聲源校準段，12～15 秒為待測段。

    .\voice.cmd calibrate --files runs/demo/A.wav runs/demo/B.wav runs/demo/C.wav --start 1 --end 10 --distances 1 1 1 --output runs/demo/clock.json
    .\voice.cmd locate --files runs/demo/A.wav runs/demo/B.wav runs/demo/C.wav --calibration runs/demo/clock.json --start 12 --end 15
    .\voice.cmd analyze --files runs/demo/A.wav runs/demo/B.wav runs/demo/C.wav --calibration runs/demo/clock.json --start 12 --end 15 --output runs/demo/analysis.json

離線校準與當次三／四軌的雜湊、取樣率和數量綁定。max_extrapolation_seconds 預設限制分析到校準段結束後 30 秒，此設定屬於離線 locate。live 的時鐘有效期由 live.json 的 max_clock_age_seconds 管理。

analyze 可用 --no-localization、--no-speaker-recognition 分別關閉功能。只做聲紋可省略校準檔案。

    .\voice.cmd analyze --files A.wav B.wav C.wav --start 0 --end 3 --no-localization --output analysis.json

analyze 仍需三個或四個實際 WAV 作為輸入。單檔聲紋辨識可使用 identify。

### 外部 STT 文字

外部 STT 以同一段發話的 utterance_id 對應，檔案內容例如下列 JSON。

    {"utterance_id":"speech-001","text":"我的筆在哪裡？"}

在 analyze 加上 --utterance-id speech-001 --stt-file stt.json。ID 不一致會拒絕合併。輸出欄位如下。

| 輸出 | 內容 |
| --- | --- |
| --output | utterance_id、interval_seconds、speaker_identity、agent_input、diagnostics、integration。 |
| --identity-output | 五欄位合併觀測。合併條件不成立或觀測時間缺少時為 null。 |
| --input-output | utterance_id、text、user_id。缺少外部文字時為 null。 |

新錄音從 summary 的 started_at_unix 加事件結束秒數取得觀測時間。舊錄音可用 --observed-at 提供實際 Unix 秒數。重新分析保留觀測時間，離線結果依其原時間評估時效。這些輸出由 CLI 保存，本機 analyze 可在 ROS 未安裝時使用。

## 已完成的三支硬體收音檢查

2026-11-22 的三支 MI-305 描述資料一致，回報單聲道、16-bit PCM、48 kHz，以及靜音、音量與 AGC 控制。當時三支 AGC 均關閉，增益讀回 -1.9921875 dB，Disable_SysFx 屬性讀回 1。

共享模式下，固定沙沙聲會逐漸壓低，其中一軌後段全為零。改用獨佔模式後，三軌持續收到固定聲音；200～8000 Hz 頻帶內，固定聲音後段比安靜段高約 15～16 dB。當時 Windows 為三個端點掛上 Voice Clarity，確切責任元件仍待單獨驗證。原始錄音亦觀察到低頻雜訊。

這些結果支持本機採用獨佔模式。換電腦後可用附帶 [30 秒沙沙聲](assets/test_audio/broadband_30s.wav) 和 [29 秒噪音／掃頻對照](assets/test_audio/noise_vs_sweep_29s.wav) 檢查完整收音。各機音效環境、靈敏度、朝向與反射仍需評估。

## 測試與後續驗收

    .\voice.cmd test

35 項軟體測試涵蓋三／四支 CLI、已知方位、時間偏移與漂移、功能四種開關組合、初始化及執行錯誤隔離、有界音訊緩衝、觀測過期，以及實際 ONNX 合成特徵推論。已知講者情境使用測試替身，真人辨識率仍待驗證。

本輪以軟體及假資料測試驗收。實際 ROS 傳輸、跨機安裝、設備長時間穩定性與真人準確度列在 [TEST_PLAN.md](TEST_PLAN.md)。live 使用固定容量滾動緩衝，持續模式的原音只保留最近一段供分析；需要完整長錄音時，需另外加入分段寫檔或長錄音支援。

## 模型及參考來源

- [WeSpeaker 官方 ONNX 推論流程](https://github.com/wenet-e2e/wespeaker/blob/master/wespeaker/bin/infer_onnx.py)
- [模型來源](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34)
- [SRP-PHAT 參考](https://pyroomacoustics.readthedocs.io/en/pypi-release/pyroomacoustics.doa.srp.html)
- [Windows AGC 介面](https://learn.microsoft.com/en-us/windows/win32/api/devicetopology/nn-devicetopology-iaudioautogaincontrol)

模型固定版本與雜湊保存在 models/model_info.json。套件及模型依各自授權使用，ROS 套件的授權欄位見其 package.xml。
