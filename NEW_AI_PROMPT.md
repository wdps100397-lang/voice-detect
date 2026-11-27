# 新 AI 接手 prompt

更新：2026-11-27。複製下方內容給接手的 AI；詳細事實以 PROJECT_HANDOFF.md 與原始檔案核對。

你正在接手王翊丞的 voice_detect 專案。請稱呼我王翊丞，用繁體中文、說人話，一次只給一個實體操作。請先實際讀檔，不能假裝看過原始碼。

本機目錄：C:\Users\王翊丞\Desktop\TMrobot\voice_detect
GitHub：https://github.com/wdps100397-lang/voice-detect

先讀 PROJECT_HANDOFF.md，再讀 README.md、TEST_PLAN.md、FOUR_MIC_EXTENSION_WORKFLOW.md，以及 config/live.json、config/array.json。音檔使用 Git LFS，必要時 git lfs pull。執行環境是 .venv-audio（Python 3.11）。

專案用三／四支 MI-305 USB 麥克風做連續聲源方向估計及聲紋辨識，將來讓 TM5-900 在固定高度依水平角度轉向講話者。手臂目前未接。Windows 收音與推論，WSL ROS 發布結果，Project A 的 LLM 只讀 identity；真實 ROS／LLM／手臂整合尚未驗收。

當次桌面配置：A 右前、B 左前、C 左後、D 右後，WASAPI 編號 [36,35,33,30]；A 直插電腦，B/C/D 經 Hub，均屬同一 USB 控制器。拔插後必須重查。X 右、Y 前、Z 上，前 0°、左 +90°。四孔座標為 [±0.1555,±0.11,0] m，校準手機 [0.155,0.310,0] m，以 JSON 的 A～D 順序為準。這不是工作台 RobotBase 正式量測。

目前最重要的問題是四路時間跳變。請讀 runs/20261125_182914_native_packet_check/test_result.json：固定聲源約第 17.25 秒起出現 256 點（5.33 ms）跳變，裝置位置和旗標卻沒有回報；補償後角度仍偏，尚未修好。通訊壅塞、裝置時鐘、驅動或韌體緩衝都是待驗證假設，不能先認定是 Hub。

src/audio_transport.py 已加入共享 RAW、可調緩衝與 MMCSS，capture.py／live.py／CLI 已接入；診斷程式只有四支開啟兩秒成功，正式入口硬體及長時間聲學測試尚待完成。49 項軟體測試通過不代表定位精度成功。網頁尚無 RAW 選項，我目前希望直接控制，不用網頁。

下一步是比較單支 A、四支共享 RAW、四支較大緩衝獨佔，入口為 runs/communication_comparison.py。這個比較還沒執行。我最後回覆暫時不方便播放，所以必須重新確認我已確實聽到沙沙聲才錄音。校準後等你通知才移動聲源；保持同一次串流，重開就重校準。附帶 30 秒噪音檔有前後各 5 秒安靜。保留所有失敗原音，不能用已知待測角度反推校準或把沒實測的功能標成成功。

請先確認目前檔案與環境，再簡短告訴我你理解的問題、能立即做的工作，以及下一個需要我配合的實體動作。不要重做已有的測試，也不要在沒確認播放時啟動錄音。
