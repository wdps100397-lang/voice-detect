# 四支 USB 麥克風定位與聲紋辨識（可切換三支）

更新日期為 2026-11-27。新接手請先閱讀 [PROJECT_HANDOFF.md](PROJECT_HANDOFF.md)。目前首要問題是四路錄音時間跳變。

此專案提供聲紋辨識、三維聲源方向估計，以及 Project A 的 ROS 2 發布節點。麥克風數量可設定為三支或四支，聲紋與定位各自可開關。Windows 負責 USB 收音與模型推論，WSL 的 ROS 節點管理辨識程序並發布結果。外部 STT 負責產生文字。

## 目前狀態

| 項目 | 實際狀態 |
| --- | --- |
| 三支／四支處理 | 收音、時間校準、方向估計與 CLI 已支援，三支及四支合成資料測試通過。 |
| 獨立功能開關 | 兩項全開、只開聲紋、只開定位、兩項全關均通過軟體測試。 |
| 功能錯誤隔離 | 模型初始化、幾何設定、校準、推論及對齊失敗的測試，確認其他可用功能可繼續輸出。 |
| 三支實體 MI-305 | 2026-11-22 完成一分鐘同錄與手機測試聲對照，三軌收到樣本，串流未回報溢位或中斷。 |
| 四支實體麥克風 | 已多輪實錄；11/25 查到 A 直插、B/C/D 經 Hub，均屬同一 USB 控制器，換接法後須重查。 |
| 實體方向精度、長時間漂移 | 未通過。固定聲源約 17.25 秒起出現 256 點跳變，48 kHz 下為 5.33 ms；裝置時間和旗標未反映。補償後最大名義角度偏差仍約 15°。 |
| 新錄音路徑 | shared RAW、可調緩衝與 MMCSS 已接入 CLI／live；診斷程式四支開啟兩秒成功，正式入口硬體及長時間聲學驗證尚待完成。 |
| 真人聲紋 | 已用單支 USB 麥克風註冊兩位講者，兩位的新錄音都成功辨識。另有一段測試音判為未知，一次盲測在選取發話區間後辨識正確。真人辨識率與 0.65／0.08 門檻仍待更多樣本驗證。 |
| 本機網頁測試台 | 已在 Chrome 驗證聲紋註冊、改 ID、辨識及錄音倒數取消。三軌、四軌合成音測試中，兩者皆將 45° 估為 46°。 |
| ROS 發布節點 | 程式及套件已提供，觀測暫存與過期邏輯通過軟體測試，實際 DDS 傳輸與 Windows／WSL 程序聯動尚待部署驗收。 |
| Project A 接收端 | LLM 只訂閱 identity。部署前需確認接收端接受 source=voice_detect 與空方位角，實際整合待驗收。 |
| 自動測試 | 涵蓋 CLI、模型推論、發話分段、聲紋補樣本、三／四支校準、不同仰角下的方位估計、功能開關及故障隔離。 |

附帶設定預設四支、兩功能全開。部署時依啟用功能填寫裝置與真人註冊資料，並將 [config/array.json](config/array.json) 的量測欄位填上現場數值。

## 取得專案與安裝

此 GitHub 儲存庫為公開。先安裝 Python 3.11 x64，包含 Python Launcher，再從 Windows 命令提示字元執行。

    git clone https://github.com/wdps100397-lang/voice-detect.git
    cd voice-detect
    git lfs install
    git lfs pull
    setup.cmd
    voice.cmd test

PowerShell 中使用 .\setup.cmd 與 .\voice.cmd test。setup.cmd 建立 .venv-audio、安裝固定版本依賴並驗證模型雜湊。Repository 已包含模型檔案，檔案缺少或雜湊不符時會重新下載。

程式的預設路徑以專案位置計算。換電腦時重新建立 Python 環境，重新確認裝置編號與實體 A/B/C/D 對應。runs 和 data 已納入 GitHub，114 個 WAV 使用 Git LFS 儲存。新資料仍須 commit／push；虛擬環境、快取、密鑰和執行中鎖檔不納入。

## 專案結構

    voice-detect/
      voice.cmd                  Windows 操作入口
      open_test_ui.cmd           雙擊開啟本機網頁測試台
      voice_app.py               CLI 與功能整合
      setup.cmd                  安裝 Python 環境與驗證模型
      requirements.txt           固定版本依賴
      src/
        capture.py               一至四個輸入的分軌錄音
        audio_transport.py       錄音模式、原生共享 RAW 與時間標記
        hardware.py              三／四個 USB 輸入的 AGC 與增益設定
        localization.py          三／四軌時間校準與左正右負方位
        speakers.py              以 Project A user_id 註冊與辨識
        features.py              功能開關、推論錯誤隔離與合併輸出條件
        live.py                  持續收音、滾動音窗與功能狀態
        events.py                離線事件、外部 STT 配對與原生 JSON
        common.py                音訊、JSON、檔案鎖與雜湊
        webui.py                 本機網頁服務與測試操作
        ui_audio.py              網頁測試用音檔處理與分析
        web/                     網頁、樣式與操作程式
      config/                    即時參數與幾何範例
      ros2/voice_detect_node/     speaker_identity ROS 2 套件
      scripts/                   模型下載與 WSL 啟動腳本
      models/                    WeSpeaker 模型與來源紀錄
      assets/test_audio/         沙沙聲與掃頻測試音檔
      schemas/                   agent/input 與合併身分 JSON 欄位契約
      tests/                     軟體回歸測試
      MI-305_CAD/                麥克風與支架 CAD
      TEST_PLAN.md               現場收音、定位與 LLM 串接驗收

mic_check.py 和 audio_controls.py 保留為早期操作入口。

## 本機網頁測試台

在 Windows 雙擊 open_test_ui.cmd，或在專案資料夾執行下列指令，即可用預設瀏覽器開啟測試台。首次使用需先完成安裝。請讓啟動視窗保持開啟，結束時按 Ctrl+C，或關閉啟動視窗。若瀏覽器沒有自動開啟，請複製啟動時顯示的網址。測試台只在這台電腦開放，錄音由專案程式透過 WASAPI 擷取。

    .\voice.cmd ui

- **聲紋辨識**可指定麥克風、錄音長度和倒數，也可匯入單聲道 WAV。在波形上拖曳或輸入起訖秒數來選擇發話區間，再調整數位增益、辨識門檻與最低分差。新講者需加入至少兩段不同錄音。替既有講者新增註冊樣本時，在名單按「補樣本」，加入至少一段新錄音，再按「更新聲紋樣本」。名單可修改 ID，也會顯示更新後的樣本數。
- **聲源定位**可選三支或四支麥克風。可用「匯入座標 JSON」載入填好的 config/array.json，或在頁面填收音孔與校準喇叭座標。可直接多軌錄音、載入資料夾，或在瀏覽器一起選取 WAV。三軌使用 A.wav～C.wav，四軌再加 D.wav。請在同一段錄音先播放固定校準聲，後面再於欲測方向播放測試聲。填入兩個區間，確認座標已量測，按「校準並估算方向」。結果會顯示在方位盤，填真值時也會顯示角度誤差。
- **保留設定**分頁、聲紋資料庫、講者選擇、麥克風、門檻、錄音與定位參數會自動寫入 data/ui_settings.json，下次開啟時恢復。首次開啟時優先使用設定的資料庫，若檔案尚未建立，會尋找本機既有的測試資料庫。

網頁使用暫存音檔，重開後請重新錄音或匯入。用 Ctrl+C 結束測試台時會清理暫存音檔。已註冊的聲紋特徵留在資料庫，辨識、定位與校準的 JSON 留在 runs。持續收音與 ROS 發布使用下方的 live 啟動流程，參數由 config/live.json 管理。

早期的聲紋資料庫只保存合併後的聲紋。對這類講者第一次補錄時，測試台會用原樣本數給舊聲紋加權，再併入新樣本。之後會保留加總後的特徵向量和來源雜湊，方便繼續補錄與避免重複加入已登記的音檔。

## 麥克風數量與功能開關

在 [config/live.json](config/live.json) 修改以下參數，重新啟動節點後生效。

    {
      "microphone_count": 4,
      "enable_speaker_recognition": true,
      "enable_localization": true
    }

microphone_count 接受整數 3 或 4，省略時也預設四支。若啟用定位，程式採用 devices 和收音孔座標的前 N 筆。先填好四支的實際資料後，往後只改 microphone_count 即可切換。

| 聲紋 | 定位 | 運行方式 |
| --- | --- | --- |
| true | true | 以 N 支收音與校準，A 軌做聲紋，所有有效定位軌計算方向。 |
| true | false | 使用 devices 第一個輸入 A，跳過幾何檔案、定位計算及時鐘校準。 |
| false | true | 使用 N 支收音與校準，跳過聲紋模型、資料庫與註冊檢查。 |
| false | false | 保持節點待命，定期輸出兩項 disabled 狀態，收音與模型保持關閉。 |

初始化或推論錯誤記為對應功能的 error，其他可用功能繼續運作。初始化設定修正後重啟節點可重新載入。持續收音預設使用 WASAPI 獨佔模式；可透過 capture_mode 選用其他已實作的錄音路徑。若設備斷線或串流溢位，worker 會結束並提供錯誤訊息，ROS 節點也會停止，需要修正設備後重新啟動。

### 其他即時設定

| 欄位 | 預設值與用途 |
| --- | --- |
| devices | 本機快照 [36,35,33,30]，依 A/B/C/D 排序；重新插拔或部署前重新核對。 |
| capture_mode | 省略為 exclusive；另支援 shared、shared_raw。shared_raw 尚未通過定位穩定性驗收。 |
| capture_latency_seconds | 選填 0.02～0.5 秒；shared_raw 省略時採 0.1 秒。緩衝變大能否改善跳變仍待測。 |
| sample_rate_hz | 48000，啟用的輸入需能開啟此格式。 |
| array_config | config/array.json，定位用幾何檔。 |
| database | data/speakers.json，聲紋註冊資料庫。 |
| window_seconds | 1.5，每輪處理最近一個音窗，可設 1.0～10 秒。即時辨識所需的語音長度是音窗的 80%，最多要求 2 秒。同一段發話會跨音窗累積有效語音，預設要累積 1.2 秒。 |
| speech_gap_seconds | 0.8，連續這麼久未偵測到語音就結束發話，可設 0.2～5 秒。 |
| vad_mode | 1，WebRTC 語音偵測嚴格度，可設 0～3。模式 1 讓較弱的語音容易被計入。現場背景聲常被誤判為語音時，可試模式 2。修改後重啟節點。 |
| update_seconds | 0.5，worker 目標更新間隔，實際速度取決於推論時間。 |
| calibration_seconds | 15，定位啟動校準長度，可設 5～30 秒。本機啟用封包跳變補償時，採最後約 6 秒校準，讓開啟串流時的漂移先穩定。 |
| calibration_distances_m | 舊版選填設定。當陣列 JSON 未包含校準喇叭座標欄位時，使用這裡直接量測的各孔聲程。 |
| max_clock_age_seconds | 0，持續使用當次時鐘模型。設正值後，過期定位會回報 error，聲紋可繼續。 |
| threshold／margin | 0.65／0.08，聲紋最高分與第二名差距的暫定門檻。 |

max_clock_age_seconds=0 代表停用時間上限檢查。長時間時鐘模型的精度仍待實測。

## Windows 收音與聲紋準備

列出裝置，選擇 Windows WASAPI 下的實體 USB 麥克風。編號可能在重新插拔或換電腦後改變，同一支也可能出現在不同音訊介面下。

    .\voice.cmd devices

三支或四支 USBAudio2.0 都連接時，可統一設定增益並關閉 AGC。

    .\voice.cmd configure

configure 要求每個裝置恰有一個可控制的 AGC 節點。目前 A 未提供相同 AGC 介面，命令可能拒絕執行，不能當作四支設定成功。歷史讀回 B/C/D 的 AGC 關閉、四支固定增益約 -1.99 dB，換接線後須重查。實體靈敏度仍需量測。

聲紋註冊使用工作台 users 的實際 ID。至少兩個不同的單聲道錄音檔，每檔需有至少兩秒被 VAD 偵測到的語音。

    .\voice.cmd enroll --user-id U_A --files sample1.wav sample2.wav
    .\voice.cmd identify --file query.wav

sample1.wav、sample2.wav、query.wav 需由使用環境準備。建議每段 5～10 秒、不同句子。模型採 WeSpeaker VoxCeleb ResNet34，輸入重取樣成 16 kHz，使用 WebRTC VAD 與 80 維 filterbank。辨識結果為 recognized、unknown 或 unusable_audio。未知身分的 user_id 為空字串，分數採餘弦相似度。

## 方向幾何與啟動校準

在 TMflow 選 `RobotBase` 和已校正的量測 TCP，依 A／B／C／D 記下各收音孔中心的位置。在 [config/array.json](config/array.json) 或網頁測試台的「手臂 RobotBase（mm）」模式直接填 TMflow 顯示的 X、Y、Z 毫米讀值。`Rx`、`Ry`、`Rz` 是工具姿態，這裡只需填位置。

| JSON 欄位 | 量測方式 |
| --- | --- |
| robot_base_positions_mm | 依 A／B／C／D 順序填收音孔中心的 `[X, Y, Z]`。收音孔須位置相異，且至少三孔不共線。三支模式可讓 D 保持空白。 |
| robot_base_calibration_source_mm | 填校準喇叭發聲位置在同一個 RobotBase 中的 `[X, Y, Z]`，以毫米填寫。程式會算出喇叭到各收音孔的直線距離。 |

填妥後將 `geometry_measured` 設為 `true`。即時收音使用的 WASAPI 裝置編號依 A／B／C／D 填在 `config/live.json`。舊版 `positions_m`、`calibration_source_m` 和選填的 `robot_frame` 仍可使用，網頁對應「陣列相對座標（m）」。舊版 `[X, Y]` 座標以 `Z=0` 讀取。

`azimuth_deg` 在 RobotBase 模式以底座 +X 為 0°、+Y 為 +90°，陣列相對模式以陣列正前方為 0°、左側為 +90°。定位採單聲源遠場模型，建議聲源距離大於陣列尺寸數倍。程式用三維座標搜尋水平角與仰角，角度輸出可細化到 0.1°，實際精度須量測。平面陣列若有上下鏡像歧義，只在可判定時回報仰角大小 `absolute_elevation_deg`。帶正負的 `elevation_deg` 需要可區分其符號。水平角不明確時回報 `ambiguous`。

使用 RobotBase 測點時，程式會自動輸出 `robot_yaw_deg`，並用各收音孔的中心平均值作為底座座標中的射線起點 `robot_ray_origin_m`。舊版陣列相對模式以 `robot_frame` 提供手臂方位轉換。陣列的水平位置若偏離底座旋轉軸，講話者離得越近，兩個原點看向同一個人的角度差越大。要用 `robot_yaw_deg` 近似 J1 目標方位，應盡量靠近旋轉軸安裝。手臂的固定 Z 與關節命令由手臂控制端設定。

定位啟動時，把喇叭放在量測的校準位置並持續播放寬頻訊號，校準期間保持喇叭與麥克風靜止。確認 status 的 clock 有校準結果、localization_error 為 null，再停止校準聲。每次重開 USB 串流重新估計初始偏移與時鐘漂移。校準段殘差過大會回報該功能錯誤。

定位使用所有麥克風配對的 GCC-PHAT 相關值，三支有三對、四支有六對。分析頻帶為 150～8000 Hz，回報 ok、ambiguous、no_signal 或 error。peak_margin 是峰值差距指標。目前硬體同步穩定性未通過，不能把 status=ok 或 0.1° 輸出解析度當成實際精度。

本機錄音中反覆觀察到約 256 個取樣點的相對時間跳變。陣列設定的 `clock_jump_samples=256` 啟用整塊補償，`0` 停用。離線 locate、網頁分析與即時 aligned_window 共用補償邏輯。每 0.1 秒估計整塊偏移，補償後檢查六組配對的延遲是否在收音孔間距允許的範圍內。至少七成音窗通過才使用該段定位。此方法要求最大聲程差小於半個補償區塊，更換設備或陣列尺寸後需要重新驗證。補償量依音訊估計，持續漂移、反射與低訊號片段仍可能使驗證失敗。

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

三個 Topic 均使用 std_msgs/msg/String，data 為 JSON 字串，QoS 為 reliable／volatile／depth 10。ROS 計時器每半秒發布仍在三秒有效期內的最新資料。timestamp 為音訊觀測時間，recognition 的 held 狀態使用上一位最後一次有效的發話觀測時間。

| Topic | 資料與條件 |
| --- | --- |
| /project_a/speaker/recognition | status、user_id、可用的分數或 reason，以及 timestamp、source。單獨聲紋模式也會提供資料。 |
| /project_a/speaker/direction | status、azimuth_deg、可用的 elevation_deg、absolute_elevation_deg、品質或 reason，以及 timestamp、source。若設定手臂座標，也發布 robot_yaw_deg、robot_ray_origin_m。無有效方向時角度為 null。 |
| /project_a/speaker/identity | LLM 使用的五欄位資料。已確認講者才填 user_id，其他狀態送空字串。方向有效時送角度，其餘送 null。 |

同一段發話一旦辨識成功，會沿用該講者 ID，直到連續一段時間沒有語音。兩人接話時若停頓未達設定秒數，會視為同一段。停止發話後，recognition 可繼續回報 held，表示上一位講者。下一段開始時，pending 表示語音累積中，unknown 表示相似度或分差未達門檻，兩者的 user_id 都是空字串。無可延續的講者時，安靜會回報 insufficient_speech。

LLM 只需訂閱 identity，以最新且三秒內的觀測決定講者。收到空 user_id 就清除上一位講者，即使 azimuth_deg 同時是 null 也應更新快取。identity 使用當次觀測時間。只開聲紋時可收到講者 ID 與 null 角度。只開定位時可收到空 ID 與有效角度。

啟動節點後，可在 WSL 查看 LLM 收到的資料。

    ros2 topic echo /project_a/speaker/identity

聲紋狀態可透過 recognition 查看。held 表示上一位講者。聲紋 disabled、error 或 unusable_audio 時，recognition 的 user_id 為空。定位 disabled、error、ambiguous 或 no_signal 時，direction 的 azimuth_deg 為 null。兩項關閉時，identity 持續發布空 ID 與 null 角度。

如果辨識一直顯示 pending，可用 `ros2 topic echo /project_a/speaker/recognition` 比對 `input_rms_dbfs`（A 軌近期平均音量）、`detected_speech_seconds`（本段已累積的辨識樣本秒數）和 `required_speech_seconds`（開始比對所需秒數）。有分數卻判 unknown 時，再查看聲紋的相似度和分差。

Project A 接收端需支援 source=voice_detect，以及 azimuth_deg 為 [-180, 180] 內的數字或 null。先前核對的 feature/live-dialogue-monitor 版本只接受 source=mock 與有限角度，尚未重新核對目前上游版本。部署時請確認實際版本支援上述欄位。

use_speaker_identity=true 時，以文字提交當下三秒內的最新觀測決定當輪身分，音訊片段的精確配對仍待整合。角度基準依量測模式而定，location 保留空字串。手臂控制端使用 direction 的底座方位。

參考來源見 [Project A 身分驗證程式](https://github.com/Ktliu-Tyler/TM-LLM-Robot/blob/feature/live-dialogue-monitor/src/project_a_llm/project_a_llm/speaker_identity.py)。

## 離線錄音與分析

record 支援一至四個輸入，長度 5～60 秒。devices 的順序決定 A/B/C/D，實際檔案數與輸入數相同。

    .\voice.cmd record --devices 28 29 33 --seconds 30

上列編號僅為三支操作範例，以當次 devices 清單替換。預設使用 WASAPI 獨佔模式，--shared 可供對照。每輪建立 runs 子資料夾，保存各軌 WAV 和 summary.json。summary 包含輸入數量、格式、裝置、開始時間、模式和串流旗標。

calibrate、locate、analyze 接受三個或四個同取樣率、單聲道 WAV。以下為使用 config/array.json 的三支範例，假定 1～10 秒為固定聲源校準段，12～15 秒為待測段。已有逐孔量測聲程時，calibrate 也接受 `--distances`，依音軌順序填寫。

    .\voice.cmd calibrate --files runs/demo/A.wav runs/demo/B.wav runs/demo/C.wav --start 1 --end 10 --output runs/demo/clock.json
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

這些是當時選擇獨佔模式的依據；11/25 四支獨佔實錄仍有時間跳變，因此不能把獨佔視為同步解法。換電腦後可用附帶 [30 秒沙沙聲](assets/test_audio/broadband_30s.wav) 和 [29 秒噪音／掃頻對照](assets/test_audio/noise_vs_sweep_29s.wav) 檢查完整收音。各機音效環境、靈敏度、朝向與反射仍需評估。

## 測試與後續驗收

    .\voice.cmd test

2026-11-27 執行的 49 項軟體測試全部通過，涵蓋三／四支 CLI、已知方位、時間偏移與漂移、功能四種開關組合、初始化及執行錯誤隔離、有界音訊緩衝、觀測過期、有效語音累積、發話分段與講者切換、舊聲紋相容與樣本補錄，以及實際 ONNX 合成特徵推論。真人辨識率仍待驗證。

目前已完成軟體回歸、三支實體收音與小規模真人聲紋測試。實際收音、定位與 LLM 串接的驗收步驟見 [TEST_PLAN.md](TEST_PLAN.md)。live 使用固定容量滾動緩衝，持續模式的原音只保留最近一段供分析。完整長錄音需要另外加入分段寫檔或長錄音支援。

## 模型及參考來源

- [WeSpeaker 官方 ONNX 推論流程](https://github.com/wenet-e2e/wespeaker/blob/master/wespeaker/bin/infer_onnx.py)
- [模型來源](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34)
- [SRP-PHAT 參考](https://pyroomacoustics.readthedocs.io/en/pypi-release/pyroomacoustics.doa.srp.html)
- [Windows AGC 介面](https://learn.microsoft.com/en-us/windows/win32/api/devicetopology/nn-devicetopology-iaudioautogaincontrol)

模型固定版本與雜湊保存在 models/model_info.json。套件及模型依各自授權使用，ROS 套件的授權欄位見其 package.xml。

## 目前桌面測試與通訊診斷

附帶 `config/array.json` 是桌面測試座標，並非工作台或 RobotBase 正式量測。A 右前 `[0.1555,0.11,0]`、B 左前 `[-0.1555,0.11,0]`、C 左後 `[-0.1555,-0.11,0]`、D 右後 `[0.1555,-0.11,0]`，單位 m。校準聲源 `[0.155,0.310,0]`。X 朝右、Y 朝前、Z 朝上，前 0°、左 +90°。

新版 record 同時保存 `timing.json`。以下使用原生共享 RAW 錄音，裝置編號須重新確認：

    .\voice.cmd record --devices 36 35 33 30 --seconds 40 --raw-shared --capture-latency 0.1

live 可在 JSON 加入 `"capture_mode":"shared_raw"` 和 `"capture_latency_seconds":0.1`。主設定目前仍省略這兩欄，沿用獨佔模式。網頁的「共享」仍是舊共享模式，沒有 RAW 選項。RAW 略過可選系統音效，驅動與硬體固定處理仍可能保留。

四支在原生獨佔模式檢查中只接受單聲道 16-bit、48 kHz，降低程式內的取樣率不能直接減少硬體 USB 傳輸。Hub 壅塞、裝置取樣時鐘與驅動緩衝行為均待區分，尚未證明根因。`clock_jump_samples=256` 是實驗補償，固定聲源仍出現錯誤角度，不能當成修復完成。

目前停止實體測試，因使用者暫時不方便播放。下次須先取得聲源已播放的明確回覆，才啟動 `runs/communication_comparison.py`；它會依序錄單支 A、四支共享 RAW、四支較大緩衝獨佔各 40 秒。`broadband_30s.wav` 含前後各 5 秒安靜，不能當成不中斷的 30 秒沙沙聲。
