# 三／四支 USB 麥克風定位與聲紋辨識

目前提供 Windows 10/11、Python 3.11 x64、CPU 執行的持續辨識程式，以及 ROS 2 speaker_identity 節點。可由持續節點辨識身分與方位，也可先錄三軌 WAV，再分析指定時間段。分析結果以 JSON 提供，方便接進機器人程式。STT 可由外部系統處理。

## 目前的重要結論

以下為 2026-11-22 在本機、三支 MI-305 接同一個 Hub 的測試結果。

| 項目 | 已確認的結果與使用方式 |
| --- | --- |
| 三支同錄 | 已完成一分鐘環境音及手機測試聲同錄，三軌均收到樣本，未回報溢位、中斷或滿幅削波。 |
| USB 回報規格 | 三支描述資料一致，支援單聲道、16-bit PCM、48 kHz，宣告靜音、音量及自動增益控制。 |
| 自動增益（AGC） | 讀取時三支原本均為關閉，重新寫入後也確認為關閉。 |
| 固定增益 | 三支讀回值均為 -1.9921875 dB，作為目前共同基準。實際靈敏度差異仍待校準。 |
| Windows 音效 | 已寫入 Disable_SysFx=1 並讀回確認；後續共享模式實錄仍觀察到持續噪音被壓低。 |
| 正式錄音模式 | voice.cmd record 預設採用 WASAPI 獨佔模式，本機對照測試中能持續收到固定沙沙聲。 |
| 定位方法 | 採用時間差估計水平方位，音量可用於輔助比較收音品質。實體時鐘校準穩定性與角度精度待驗證。 |
| 聲紋辨識 | 模型與程式流程已通過無人聲測試，真人辨識率、未知者誤接受率與門檻待驗證。 |

### 為什麼正式錄音使用獨佔模式

手機完整播放固定沙沙聲時，共享模式錄音主要保留開頭，之後逐漸壓低。第二輪對照中，固定噪音後段的 B 軌全為零，後面的變化掃頻聲則能持續收錄。當時三支 AGC 均關閉、增益一致、系統音效停用屬性已寫入，而且串流錯誤旗標均為空。

改用獨佔模式、沿用手機位置與音量重播後，三支都持續收到固定噪音。以 200～8000 Hz 頻帶量測，固定噪音後段比安靜段高約 15～16 dB。Windows 為三個輸入端點掛上 Voice Clarity 音效元件，這是目前的可疑來源；對照測試支持共享收音路徑會影響內容，確切元件仍待單獨驗證。

因此，每台電腦都應以實際播放聲確認收音內容，同時檢查 summary.json 的 exclusive_mode。三軌收到非零樣本與設定值讀回正確，只能完成基本檢查，持續聲音是否被改變還要回放或分析波形。原始錄音也觀察到低頻雜訊，頻帶內訊號品質需另外計算。

硬體測試的重要數據已整合於本節。完整共享／獨佔對照與原音保存在本機 runs，交接時可按需要另行提供。

### 定位與安裝的決策

時間差法是目前主要實作方向。同一個 Hub 與相同 48 kHz 格式仍需配合三軌起錄偏移、時鐘漂移校準。合成測試已驗證校正與方位搜尋流程，現場可先以角度誤差 15° 內作為原型驗收目標，實際精度待量測。

音量法可先用於粗略判斷靠近哪支麥克風。以 20 公分間距估算，理想自由場下聲源距離陣列中心 1 公尺時，兩支最大音量差約 1.74 dB；2 公尺時約 0.87 dB。麥克風靈敏度、朝向與反射都會影響這個差距，目前尚無實測角度精度。

正式安裝以三支小孔面朝上、相同朝向與高度、收音孔露出作為初始基準，用支架固定並減少桌面碰觸和線材拉動。座標以實際收音孔位置量測。這批麥克風的方向性尚待測試，旋轉、翻面及貼近桌面的差異也需納入驗證。

## 從 GitHub 取得專案

此專案使用私有 GitHub repository，協作者取得權限後可下載。以 Git 複製的方式如下。

    git clone https://github.com/wdps100397-lang/voice-detect.git
    cd voice-detect
    setup.cmd
    voice.cmd test

先安裝 Python 3.11 x64。Repository 包含程式、ROS 套件、模型、設定範例、測試音檔及 MI-305_CAD。setup.cmd 會在本機建立 Python 環境並驗證模型雜湊。本機錄音及測試紀錄保存在 runs，真人註冊資料保存在 data，這兩個資料夾由各使用環境自行保存。

## 專案結構

```text
voice_detect/
  voice.cmd                  統一操作入口
  voice_app.py               指令與兩項功能整合
  setup.cmd                  在本機建立 Python 環境及取得模型
  requirements.txt           固定版本依賴
  src/
    capture.py               三個裝置分軌錄音及異常紀錄
    hardware.py              AGC、固定增益及 Windows 音效設定
    localization.py          時鐘校準及 Project A 左正右負方位
    speakers.py              以 Project A user_id 註冊及辨識
    events.py                直接產生 Project A 原生事件與診斷
    live.py                  三／四軌收音、獨立功能開關與啟動校準
    common.py                音訊讀寫、雜湊及 JSON 儲存
  config/array.json          三個收音孔實際座標
  config/live.json           麥克風數量、功能開關及即時辨識參數
  ros2/voice_detect_node/    speaker_identity ROS 2 套件
  assets/test_audio/         固定噪音與掃頻測試 WAV
  models/                    WeSpeaker 模型及來源、版本、雜湊
  data/                      聲紋資料庫，註冊時建立
  runs/                      每次錄音、設定前後快照和測試結果
  scripts/download_model.py  固定版本模型下載及完整性檢查
  schemas/                   agent/input 與真實 speaker/identity 欄位契約
  tests/                     無需真人說話的回歸與事件契約測試
```

原有 mic_check.py 和 audio_controls.py 保留為相容入口。所有預設儲存路徑以專案所在位置計算。


## 麥克風數量與功能開關

在 config/live.json 調整下列三個參數，重新啟動節點後生效。

    {
      "microphone_count": 3,
      "enable_speaker_recognition": true,
      "enable_localization": true
    }

microphone_count 接受 3 或 4。devices、positions_m、calibration_distances_m 可先填齊四支，程式依數量使用前 N 支。四支的實際裝置編號、收音孔座標和校準聲程都填好後，只改 microphone_count 就能切換。附帶的第四個座標是範例，geometry_measured 需在實際量測完成後設成 true。

| 聲紋 | 定位 | 運行方式 |
| --- | --- | --- |
| true | true | 三支或四支同錄並校準，各自計算身分和方向。 |
| true | false | A 麥克風收音並辨識身分，跳過幾何檔案及時間校準。 |
| false | true | 三支或四支同錄並校準，計算方向，跳過聲紋模型與註冊資料。 |
| false | false | 節點保持待命，回報 disabled 狀態，收音與模型均保持關閉。 |

定位初始化或校準失敗時，聲紋仍可工作；聲紋模型或資料庫初始化失敗時，定位仍可工作。推論過程中的錯誤也各自記錄，下一個音窗繼續嘗試；修正初始化設定後重啟節點可重新初始化。

### 各功能的獨立 Topic

以下三個 Topic 使用 std_msgs/msg/String，reliable／volatile／depth 10。

| Topic | 內容與可用時機 |
| --- | --- |
| /project_a/speaker/recognition | 聲紋的 status、user_id、分數或錯誤原因，附 timestamp、source。單獨啟用聲紋也持續輸出。 |
| /project_a/speaker/direction | 定位的 status、azimuth_deg、品質或錯誤原因，附 timestamp、source。定位 disabled／error 時角度為 null。 |
| /project_a/speaker/identity | 保留現有五欄位合併契約。角度有效且聲紋 recognized／unknown 時輸出；單獨定位時 user_id 為空。 |

聲紋關閉時 user_id 為空，狀態是 disabled；偵測到未知講者時狀態是 unknown。關閉或無法估算定位時，direction Topic 的 azimuth_deg 為 null。各接收端先檢查 status 與 timestamp，再使用數值。

只啟用聲紋時，原有 identity Topic 等待可用角度，身分由 recognition Topic 提供。既有 Chat 若要在此模式使用身分，需要接收 recognition Topic；目前的 Chat 只訂閱 identity。兩項均關閉時，獨立 Topic 會回報 disabled，原合併觀測依三秒有效期自然失效。

離線 analyze 也可以獨立切換。以下只做聲紋，可省略校準檔案。

    .\voice.cmd analyze --files A.wav B.wav C.wav --start 0 --end 3 --no-localization --output analysis.json

只做定位可加 --no-speaker-recognition，兩項都關閉可同時加兩個選項。calibrate、locate、analyze 的 --files 依實際提供的三個或四個 WAV 決定數量；calibrate 的 --distances 需一檔對應一個值。record 的 A/B/C/D 順序由 --devices 決定。

## 搬到另一台電腦

1. 在新電腦安裝 Python 3.11 x64，包含 Python Launcher。
2. 複製整個專案。程式、config、models 和已註冊的 data 可以一起帶走。runs 可視需要複製。虛擬環境 .venv 和 .venv-audio 由新電腦重新建立。
3. 在專案資料夾執行 `setup.cmd`。安裝套件需要網路，模型檔已存在且雜湊正確時直接沿用。
4. 插好三支麥克風，執行 `voice.cmd devices`，找 Windows WASAPI 的三個 USBAudio2.0 輸入編號。每台電腦及重新插拔後都要重新確認。
5. 執行 `voice.cmd configure`。程式備份原設定，將偵測到的三支或四支 AGC 關閉，增益統一到第一支現有值，關閉 Windows 系統音效，並讀回驗證。若權限或驅動不支援，會回報錯誤，結果留在 runs。
6. 用手機在固定位置播放測試 WAV，先驗證三支獨佔同錄完整收到持續聲音，再進行定位或聲紋測試。新電腦的音效元件與處理可能不同，當次結果存入 runs。

重新插拔後曾出現裝置名稱由「3- USBAudio2.0」變成「4- USBAudio2.0」的情況，裝置編號與 A/B/C 對應也可能變更。為三支本體與接線貼標籤，重新確認實體對應後再使用座標與聲紋參考軌。跨電腦或重新開始錄音時，需用當次三軌建立時間校準。

裝置列表中的 USBAudio2.0 是裝置名稱，名稱本身不能判定 USB Audio Class 版本。本次實際描述資料的音訊類別版本為 1.0。

## 收音

在 PowerShell 中使用 ` .\voice.cmd ` 呼叫入口。以下 28、29、33 是本機曾列出的編號，執行前以 devices 當次清單確認。A/B/C 依 --devices 的順序對應。

```powershell
.\voice.cmd devices
.\voice.cmd configure
.\voice.cmd record --devices 28 29 33 --seconds 30
```

每輪建立獨立 runs 子資料夾，依實際數量存 A.wav、B.wav、C.wav、D.wav 及 summary.json。record 支援一至四個輸入、5–60 秒。此階段可錄環境音，先確認非零樣本、沒有溢位和中斷。三軌是各自開啟的 USB 串流，開始時間會有偏移。

voice.cmd record 預設使用 WASAPI 獨佔模式，請選擇 devices 清單中的 Windows WASAPI 輸入。本機實測共享模式會逐漸壓低持續沙沙聲，獨佔模式能持續收錄，因此正式測試採用獨佔模式。其他程式占用裝置時可能無法開啟獨佔錄音，請關閉占用程式後重試。--shared 可供對照測試。summary.json 的 exclusive_mode 記錄當次模式。相容入口 mic_check.py 仍可用 --exclusive 明確選擇獨佔模式。

固定擺放前可做連續同錄、環境底噪、同一手機聲源下的收音差異、翻轉方向及重新插拔後的設定檢查。手機外放位置及音量保持固定，依提示播放完整音檔。

| 測試音檔 | 播放內容 |
| --- | --- |
| [broadband_30s.wav](assets/test_audio/broadband_30s.wav) | 安靜 5 秒、固定寬頻噪音 20 秒、安靜 5 秒。 |
| [noise_vs_sweep_29s.wav](assets/test_audio/noise_vs_sweep_29s.wav) | 安靜 3 秒、固定噪音 10 秒、安靜 3 秒、變化掃頻 10 秒、安靜 3 秒。 |

原始錄音保留低頻雜訊。定位分析會在相關計算中保留 150～8000 Hz 頻帶，做訊號品質比較時也應使用相同頻帶，避免低頻底噪掩蓋實際測試聲。

## 定位

量三個收音孔的水平座標，填進 config/array.json 的 positions_m，再把 geometry_measured 改為 true。單位為公尺，x 朝右，y 朝前。azimuth_deg 採 Project A 規格，正前方 0°、左側 +90°、右側 -90°，範圍 [-180,180]。檔案中的三角形座標是起始範例，請以實際安裝位置替換。

第一版採用單聲源、同高度、遠場水平角度模型。聲源距離應大於陣列尺寸數倍。三支或四支收音孔需採非共線配置。延長線到位前，可以做收音及軟體測試。

每次重新開始 USB 錄音都要重新校準。在同一段連續錄音中，先將單一寬頻聲源固定，記錄它到三孔的實際距離，持續播放數秒以上供校準，再於同一錄音的後段測方位。三支到校準聲源等距時，可將三個距離填相同值。校準時保持聲源不動，優先使用寬頻噪音或掃頻，單頻嗶聲可能有多個相似延遲峰值。

以下假定 runs/demo 已放入同一次錄音的三軌，前 1–10 秒為校準段，12–15 秒為待測段，校準聲源到三孔都為 1 公尺。

```powershell
.\voice.cmd calibrate --files runs/demo/A.wav runs/demo/B.wav runs/demo/C.wav --start 1 --end 10 --distances 1 1 1 --output runs/demo/clock.json
.\voice.cmd locate --files runs/demo/A.wav runs/demo/B.wav runs/demo/C.wav --calibration runs/demo/clock.json --start 12 --end 15 --output runs/demo/direction.json
```

校準估計初始偏移與時鐘漂移，再用三對 GCC-PHAT 相關值搜尋方位。校準殘差過大會拒絕輸出校準資料，指向峰值太接近時回報 ambiguous，安靜時回報 no_signal。peak_margin 是峰值差距指標。校準與三軌檔案雜湊綁定，下一次錄音需產生新的校準。預設只允許外推到校準段結束後 30 秒，這個範圍仍需用實機驗證。

## 聲紋

採用 WeSpeaker VoxCeleb ResNet34 預訓練模型，以 CPU 推論。先把輸入轉成 16 kHz，以 WebRTC VAD 篩選語音，再計算 80 維 filterbank 和說話者向量。這個模型是否適合你們的中文、距離及現場噪音，需要真人測試。

每位成員至少提供兩個不同的單聲道錄音檔，每檔需有至少兩秒被偵測到的語音。建議每檔 5–10 秒，先固定使用 A 軌，錄不同句子。範例的 sample1.wav、sample2.wav、query.wav 由之後的真人錄音提供。

```powershell
.\voice.cmd enroll --user-id U_A --files sample1.wav sample2.wav
.\voice.cmd identify --file query.wav --threshold 0.65 --margin 0.08
```

辨識取與註冊模板的餘弦相似度。最高分需達 threshold，且與第二名差距需達 margin，才回報 recognized 和 user_id。其餘回報 unknown。沒有足夠語音或削波時回報 unusable_audio。VAD 對環境聲仍可能誤判。

0.65 和 0.08 是暫定起始門檻，需以另一場次的已知及未知講者錄音調整。分數可供比較，無法當成正確率。資料庫記錄模型雜湊，換模型時需要重新註冊。註冊寫入設有檔案鎖與完整檔案替換，若另一個程序正在寫入會回報稍後重試。

## 同一段發話同時分析

```powershell
.\voice.cmd analyze --files runs/demo/A.wav runs/demo/B.wav runs/demo/C.wav --calibration runs/demo/clock.json --start 12 --end 15 --output runs/demo/event.json
```

analyze 使用同一事件區間，方位使用三軌校正後音訊，講者辨識固定使用 A 軌。定位與聲紋各自保留診斷，任一項失敗時仍輸出其餘可用結果。輸出含 utterance_id、speaker_identity、agent_input、diagnostics 和 integration。聲紋註冊直接使用工作台 users 的 ID，例如 U_A。未知身分的 user_id 為空字串。

## Project A 原生事件介面

對接依據為 feature/live-dialogue-monitor 分支 commit 41003af729f6e755796cf1092086e33bc72b0b4e，以及使用者提供的 2026-11-23 NODE_ARCHITECTURE.md、NODE_API_CONTRACTS.md。現有 Chat 使用 Ollama qwen3.5:4b，負責合併身分、方向與外部 ASR 文字，進行工具呼叫。本專案直接產生其語音入口及身分觀測資料。

| 輸出 | 對應 Topic | 內容 |
| --- | --- | --- |
| --input-output | /project_a/agent/input | utterance_id、text、user_id。缺少外部 STT 時為 null。 |
| --identity-output | /project_a/speaker/identity | user_id、location、azimuth_deg、timestamp、source。角度或觀測時間不可用時為 null。 |
| --output | 本機診斷檔 | 兩項原生 payload、各功能狀態、分數、錄音來源雜湊與接入限制。 |

兩個 Topic 均為 std_msgs/msg/String，data 使用 JSON 字串，QoS 為 reliable／volatile／depth 10。輸出 payload 可以由 ROS 發布端直接序列化。空值檔案表示當次沒有可發布觀測，發布端需跳過。

外部 STT 提供以下格式，utterance_id 與本次 --utterance-id 一致。worker 的 phase=transcript 事件需由其管理端帶上同一段音訊的 utterance_id，才能確認文字屬於本次音訊。

```json
{"utterance_id":"speech-001","text":"我的筆在哪裡？"}
```

```powershell
.\voice.cmd analyze --files runs/demo/A.wav runs/demo/B.wav runs/demo/C.wav --calibration runs/demo/clock.json --start 12 --end 15 --utterance-id speech-001 --stt-file runs/demo/stt.json --output runs/demo/analysis.json --identity-output runs/demo/identity.json --input-output runs/demo/input.json
```

新錄音的 summary.json 保存 started_at_unix；觀測 timestamp 採錄音起始時間加事件結束秒數。舊錄音缺少該資訊時，可用 --observed-at 提供實際觀測 Unix 秒數。保留原始事件時間，分析完成時間另用於計算 identity_age_seconds。上游只接受 0～3 秒內觀測，離線分析結果需評估時效。

目前有三項實際接入限制。

- 上游驗證器只接受 source=mock。本專案真實觀測的 source 固定為 voice_detect，接收端支援此來源後才能接入聲紋 Topic。目前 integration.identity_receiver_compatible 為 false，欄位 schema 的描述也標記了這個缺口。
- use_speaker_identity=false 時，上游 Chat 可使用 agent/input.user_id；true 時會依最新身分觀測覆蓋。上游目前以文字提交當下 3 秒內最新觀測做時間近似配對，精確音訊片段對齊仍需共同整合。
- 本專案已提供持續收音及每 0.5 秒發布的 ROS 節點，軟體測試驗證處理流程。Jarvis 自有語音文字走內部 submit，接入時由其現有流程取得身分 Topic。

location 目前固定為空字串，保留未提供的位置標籤。方位是相對麥克風的水平角，手臂世界座標與交付區需由後續系統定義。speaker/identity payload 沒有置信度欄位，分數與錯誤原因保存在 diagnostics；聲紋相似度及方位峰值差距各自維持量測指標。

本輪將定位輸出改為 azimuth_deg、辨識輸出改為 user_id／recognized、事件識別改用 utterance_id。較早 runs 中的 angle_degrees、speaker 等欄位屬於歷史測試格式，重新分析原始 WAV 即可產生目前格式。



## 真實 speaker_identity 節點

已核對 GitHub feature/live-dialogue-monitor 最新 commit 93d9bc1af753deadd4ab2c5487438e2cc765301c。此節點負責取代 mock_speaker 的資料來源，發布到同一個 /project_a/speaker/identity Topic。

節點啟動 Windows 收音／辨識 worker，啟用定位時，依設定數量以三支或四支 USB 的 WASAPI 獨佔串流持續收音。啟動時用固定寬頻聲源估初始偏移與漂移；之後每 0.5 秒分析最近 2.5 秒的校正音訊。A 軌辨識 user_id，所有啟用的定位麥克風一起估 azimuth_deg，原生五欄位觀測由 ROS 節點直接發布成 String.data JSON。

ROS 端管理 worker 啟停與觀測時效。兩個程序是同一套節點的組成部分，觀測資料沿用相同五欄位。QoS 為 reliable／volatile／depth 10。timestamp 保留實際音訊時間，節點每 0.5 秒發布仍在三秒有效期內的最新觀測。安靜、語音不足、方向不明或推論過慢時，等待新的有效音訊，原觀測自然過期。各功能的初始化與推論錯誤會保存在獨立狀態，其他可用功能繼續處理。共享收音的串流溢位或設備斷線會結束 worker 並回報原因，重新啟動會重新校準。

### 設定

依啟用功能準備設定。定位使用實際幾何及校準聲程，聲紋使用註冊資料。各功能關閉時，對應的檔案與初始化會一起跳過。

1. 量實際使用的三支或四支座標填 config/array.json，設 geometry_measured=true。
2. 執行 voice.cmd devices，依實體 A/B/C/D 順序，把 WASAPI 編號填進 config/live.json 的 devices。
3. 以 voice.cmd enroll --user-id U_A --files ... 註冊實際工作台 user_id，每位至少兩段不同錄音。
4. 填 calibration_distances_m，代表固定校準聲源到各收音孔的公尺距離。
5. 執行 voice.cmd configure。啟動校準時維持寬頻聲源固定，持續播放至 phase=ready，再停止校準聲。

calibration_seconds 預設 8 秒，window_seconds 預設 2.5 秒，update_seconds 預設 0.5 秒。max_clock_age_seconds=0 表示持續使用當次啟動的時鐘模型，可依後續量測的穩定時間設定有效秒數，到期後重新啟動。移動講者的聲程差需要保留，時鐘校準使用固定聲源。

Windows worker 可由本專案入口啟動。

    .\voice.cmd live --config config/live.json

stdout 使用 UTF-8 JSON Lines。kind=status 顯示 calibrating、ready、observed、insufficient_speech 等狀態；kind=identity 的 data 是 Topic 五欄位資料；kind=error 提供停止原因。

### WSL 的 ROS 節點啟動

ROS 環境需有 ROS 2 Humble、rclpy、std_msgs、colcon。Windows 的 .venv-audio 提供收音與模型依賴，WSL 端 ROS 套件管理程序與發布資料。在 WSL 可從本專案獨立建置。

    cd '/mnt/c/Users/王翊丞/Desktop/TMrobot/voice_detect'
    source /opt/ros/humble/setup.bash
    colcon build --base-paths ros2 --build-base runs/ros_build --install-base runs/ros_install
    source runs/ros_install/setup.bash
    bash scripts/run_speaker_node.sh

ROS_DOMAIN_ID 沿用 Project A 其他節點。啟動腳本自動取得本專案位置、Windows Python 及 Windows 路徑，交由 ROS 節點啟動 worker。先停止 mock_speaker_identity，讓真實發布端單獨提供身分觀測。

另一個已載入 ROS 環境的終端可查看輸出。

    ros2 topic echo /project_a/speaker/identity

最新版接收端仍只接受 source=mock，此真實節點使用 source=voice_detect。接收端支援真實來源後，Chat 的 use_speaker_identity=true 才能使用其觀測。

### 本輪軟體驗證

voice.cmd test 現有三十五項測試，包含有界音訊緩衝、絕對樣本位置、時鐘校正的滾動音窗、合成方位進入 ROS 觀測暫存、未知／安靜／過期處理，以及既有模型與 CLI 測試。實際 DDS 傳輸、Windows／WSL 程序與設備環境仍需部署時核對。本輪依使用者指示，以程式和假資料驗證。

目前 config/live.json 的 devices、實際幾何與真人註冊資料需依啟用的功能在部署時填寫。這些是啟動所需的設備設定。

## 無人聲測試

可直接執行 `.\voice.cmd test`，使用合成音訊、假身分和測試文字跑整套本機測試。新增 CLI 測試會產生模擬左前方 45° 的三軌 WAV，從校準、分析到原生 JSON 輸出走正式入口，並檢查錯誤的文字事件編號會被拒絕。

```powershell
.\.venv-audio\Scripts\python.exe -m unittest discover -s tests -v
```

涵蓋八個已知方位、安靜時無方向、已知時鐘偏移與漂移的三軌 WAV 校準、錯用校準檔的拒絕、語音不足的拒絕、已知／未知／分數接近的聲紋比對，以及實際 ONNX 模型的合成特徵推論。這些結果驗證程式流程，現場角度誤差與真人辨識正確率待實測。

目前三十五項自動測試均通過；已將程式入口、src 與 config 複製到另一個含空白的路徑，使用既有 Python 環境驗證啟動與裝置列舉。另一台實體電腦的全新安裝仍待驗證。

## 待測清單

建議依下列順序完成並在 runs 保存條件、原音和評分。表內通過門檻沿用 TEST_PLAN.md 的原型建議值，正式需求可再調整。

| 完成 | 順序與項目 | 需要條件 | 操作與驗收重點 |
| --- | --- | --- | --- |
| [ ] | 1. 實體 A/B/C 對應 | 現有 Hub、三支麥克風、標籤 | 逐支插入，記錄新增輸入與本體標籤，建立當次 --devices 順序。確認實際位置與程式聲道一致。 |
| [ ] | 2. 插拔及重開機後設定 | 可拔插 Hub，重開機另安排 | 每種情境先讀取 AGC、增益及音效設定並記錄，再執行 configure。確認三支重新可用、設定可套用、獨佔模式可同錄，播放測試聲仍完整。 |
| [ ] | 3. 獨佔收音重複性 | 手機外放，固定位置及音量 | 固定噪音／掃頻重做至少三輪，查看三軌整段波形、頻帶內音量變化及短暫靜音，確認持續聲音能重複收錄。若三軌共同變小，需另查手機播放端。 |
| [ ] | 4. 個體差異與擺放方向 | 手機與臨時位置標記 | 依序把各支放在同一個標記位置、相同朝向，播放相同聲音比較，再用同一支測朝上、側放與翻面。以此量實際收音差異，選定固定朝向及高度。 |
| [ ] | 5. 真實三軌時間校準 | 聲源與三支保持靜止，量聲源至各孔距離 | 獨佔同錄，用前段估初始偏移與漂移，以後段已知聲程驗證校正殘差，再重開串流重做。先以各對殘差絕對值第 95 百分位 ≤ 0.10 ms 為目標，確認無突跳。 |
| [ ] | 6. 實體方位盲測 | 延長線、固定三角形、座標量測 | 每 45° 一點，共八方向，1 m／2 m、安靜／現場噪音各測。報有效輸出率、中位誤差、第 90 百分位及前後判反。初驗目標依序為 ≥ 90%、≤ 15°、≤ 30°。 |
| [ ] | 7. 真人聲紋及未知者 | 成員方便錄音，另有未註冊者參與 | 分場次收集註冊、調門檻及盲測語音，先固定 A 軌，再比較其他麥克風。報已知答對、錯認、遭拒及未知誤接受次數。初驗目標為已知答對 ≥ 90%、未知誤接受 ≤ 5%，同時列分母。 |
| [ ] | 8. 整合及換電腦 | 前項校準與註冊完成，另一台 Windows 電腦 | 同一事件檢查方向、身分及回應時間。新電腦從 setup.cmd 安裝，重新辨識聲道、設定收音、量測校準，再跑相同測試。確認搬移後結果可用。 |

延長線到位前可先做第 1～5 項，其中第 5 項先驗證時鐘校準。幾何位置拉開後，再用實際三角形完成第 6 項。第 7 項可在方便錄人聲時獨立進行。

長時間穩定性可使用 live 持續模式檢查 USB 中斷、時鐘漂移與校準有效時間。record 檔案錄音入口上限仍為 60 秒；live 採有界滾動緩衝，持續運行時維持固定音訊記憶體用量。本輪依指示以軟體測試驗證。

Voice Clarity 與共享模式抑制的確切關係仍待單獨對照驗證。目前正式流程採用已測過的獨佔模式，這項原因追查可另行安排。

## 來源

- [WeSpeaker 官方 ONNX 推論流程](https://github.com/wenet-e2e/wespeaker/blob/master/wespeaker/bin/infer_onnx.py)
- [WeSpeaker 模型來源](https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34)
- [Pyroomacoustics SRP-PHAT 說明](https://pyroomacoustics.readthedocs.io/en/pypi-release/pyroomacoustics.doa.srp.html)
- [Microsoft AGC 控制介面](https://learn.microsoft.com/en-us/windows/win32/api/devicetopology/nn-devicetopology-iaudioautogaincontrol)

此專案自行實作相關值計算和方位搜尋，聲紋特徵參數依 WeSpeaker 官方推論範例設定。模型來源與固定版本在 models/model_info.json。轉交專案時一併檢視上游模型及套件的授權條件。
