# 實測資料與音檔快照

更新：2026-11-27。完整設定與交接見 [PROJECT_HANDOFF.md](../PROJECT_HANDOFF.md)，部署清單見 [TEST_PLAN.md](../TEST_PLAN.md)。49 項軟體回歸已通過，錄音模式比較尚未執行。

本目錄保存麥克風錄音、校準設定、分析結果與診斷程式。WAV 音檔使用 Git LFS 儲存；複製儲存庫後請執行 `git lfs pull` 取得完整音檔。

錄音包含不同測試條件，也包含未開始播放、播放中斷及校準失敗的紀錄。請配合各資料夾中的 `RESULT.md`、`test_status.json`、`test_result.json`、`summary.json` 或分析結果判讀，不能把所有音檔都視為有效的定位測試。

目前已確認四路錄音有時間跳變，原有補償尚未達到可靠的精確角度定位。`20261125_182914_native_packet_check/test_result.json` 記錄固定聲源的失敗結果。新的共享 RAW 錄音路徑已完成裝置開啟檢查，完整聲學驗證與不同錄音模式的比較仍待執行。

`communication_comparison.py` 會實際啟動錄音，執行前必須先確認固定聲源已在指定位置持續播放。裝置索引會隨拔插變動，請重新核對 A～D 的對應。

目前音檔共 114 個（含 assets 兩個測試音），其中 runs 有 112 個。`20261125_182816_native_packet_check/test_status.json` 已標記播放未開始，不可用來校準。`20261125_184101_native_packet_check_shared_raw_4ch/` 僅為兩秒裝置開啟檢查。不要重命名或覆寫原始證據；重新分析另存結果並記錄方法。
