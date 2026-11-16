# Huh? 雙人分割字幕畫面

User A / User B 各固定占一半，可選左右或上下，使用圓角卡片。字幕以歌詞列表呈現，最新一段亮起並自動跟隨，每段附辨識完成的本地時間與請求編號。往上捲動可查看歷史，按「回到最新」恢復跟隨。新音檔失敗時保留成功字幕並提示錯誤。字級提供 12～72（每次調整 1），可選深色或淺色模式；字級、排列與外觀會記住在目前瀏覽器。

歷史保留範圍：目前開啟的頁面持續累積已收到的成功字幕；重新整理後恢復伺服器最近 30 筆請求內的字幕，以及 A / B 各自最後一次成功字幕。伺服器重啟會開始新的工作階段並清除畫面歷史。舊伺服器若未提供完成日期，時間戳會標為「工作階段」經過時間。

## 安裝
將 ZIP 內檔案解壓縮到 C:\桌面!!\EOS\EOS_final project。
新版需一起部署 server_fcfs_display.py、subtitles.html、audiowave.gif，也可使用新版 Huh_Subtitle_Display.zip。原 EOS_Subtitle_Display.zip 保留為舊版備份。

Windows PowerShell:
```powershell
Set-Location 'C:\桌面!!\EOS\EOS_final project'
scp .\server_fcfs_display.py .\subtitles.html .\audiowave.gif thelreneyang@192.168.222.222:~/EOS_Final_Project/
```
Pi 原 Server 終端機 Ctrl+C 後:
```bash
cd ~/EOS_Final_Project
.venv/bin/python server_fcfs_display.py
```
Windows 瀏覽器開 http://192.168.222.222:8000/display，已開啟的頁面重新整理。可切換排列、投放模式、全螢幕。

沿用現有 client_fcfs.py，不需更新，不需安裝新套件。請確保 --client-id 為大寫 A / B，其他 ID 不會出現在這兩個固定區域。

A:
```powershell
py -3 .\client_fcfs.py 'C:\Users\irene\Downloads\UncleRoger.wav' --client-id A
```
B:
```powershell
py -3 .\client_fcfs.py .\Client_B.wav --client-id B
```

## 沒有麥克風時，預覽收音動畫
```powershell
py -3 .\client_status.py --client-id A --seconds 10
```
在另一個視窗可同時執行:
```powershell
py -3 .\client_status.py --client-id B --seconds 10
```
只模擬收音狀態，沒有錄音或辨識。結束時自動清除；若程序中斷，最後一次回報 5 秒後過期。真正 WAV 上傳只顯示上傳/排隊/辨識，不會假裝正在收音。區網上傳通常很快，500 ms 輪詢不一定看得到上傳狀態。

## 未來接麥克風的介面
Client 持續擷取音訊；偵測語音時回報 recording，每 1.5 秒更新；語音段結束回報 idle，將完整 WAV 用原本 /transcribe 上傳。FCFS 仍依完整請求入列順序，不以開始說話時間排隊。

POST /api/client-state, Content-Type: application/json:
```json
{"client_id":"A","state":"recording"}
```
結束改為 state: idle。每次 recording 回報續期 5 秒。這是狀態介面，尚未實作麥克風錄音或自動語音偵測。波形使用同目錄的 audiowave.gif，由 Server 的 /audiowave.gif 提供，不需外網；僅於語音輸入中顯示，斷線時隱藏。瀏覽器若啟用減少動態效果，會隱藏 GIF、保留狀態文字。動畫不代表真實音量。

規劃：先完成按鍵錄音與音訊裝置驗證，再用語音活動偵測 (VAD) 自動開始/結束一段音訊，加上句首緩衝、短雜音過濾、最長片段限制及有界上傳佇列。ASR 等待期間仍應持續擷取下一段。停頓門檻需在真實環境測試。

本版仍是整段完成後顯示字幕，尚未做邊說邊出字。顯示收音動畫不會讓 Server 提前開始辨識。

## 驗證範圍
本次以本機替代辨識器驗證辨識完成時間、GIF 和投放頁路徑。Python 與網頁 JavaScript 語法檢查通過。以 Edge 無頭瀏覽器驗證字幕歷史、User A / B 隔離、失敗保留字幕、暫停捲動及回到最新、深淺色與排列偏好保存、12 / 72 字級端點、GIF 載入、小螢幕無水平溢出及工作階段重設，並檢視深色／淺色／手機截圖。實際 Pi / Vosk 與麥克風仍需硬體實測。
