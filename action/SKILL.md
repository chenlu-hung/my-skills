---
name: action
description: Schedule a one-shot Claude Code task to start at a specific wall-clock time and run headless/unattended, so work that has to happen while the user is asleep or away actually happens. Two main uses. First, unattended watch-and-wrap-up on remote work — wake at a set time, check whether a cloud/GPU run finished, pull the results back, verify them, then destroy the instance so it stops billing. Second, moving heavy or batch work into an off-peak window to spread load across the rolling usage limits. Use when the user says "睡前", "明天早上起來", "幾點起來看", "跑完就把機器關掉", "抓回來再關掉", "別一直燒錢", "監控到跑完", "排程", "離峰時段跑", "分攤額度", "wake up at 8am and check", "shut the instance down when it finishes", "schedule this to start at 3am", "defer until …", or invokes "/action". Anything the user wants done after the current session ends MUST be scheduled here, never promised in-session. NOT for truly recurring cron-style jobs (use /schedule or /loop) or for resuming after a limit is already hit (that is autocontinue).
---

# action — 排程在指定時間才啟動的 headless 任務

把一個任務 park 起來，到指定的**絕對時鐘時間**才用 `claude -p` 在背景 headless 跑（預設無人值守、skip permissions）。它讓「人不在的時候仍然要發生的事」真的會發生——半夜把跑完的雲端實驗收回來並關機停止計費、清晨把粗重工作挪進離峰額度視窗。

這是 `autocontinue` 的**主動版**：autocontinue 是撞到 limit 後等 reset 自動接續（被動）；action 是事先排到指定時間才開始（主動）。兩者可疊加——排程跑的 job 若中途撞 limit，全域安裝的 autocontinue StopFailure hook 會接手復活。

## 鐵律：跨越 session 的事一律排程，不准口頭承諾

使用者要求任何在**本次對話結束之後**才發生的事——「等它跑完再抓回來」「明天早上八點看一下」「跑完就把機器關掉」——都必須排成 action job。

session 在那個時間點**不存在**。在對話裡回「好，我到時候會處理」「我會持續監控」是空頭支票：終端機關掉、電腦鎖上、context 被清掉，承諾就隨之消失，而遠端實例會繼續計費到有人發現為止。in-session 的 background bash、`sleep`、Monitor 全部同樣會死。

判準只有一句：**這件事要發生的時刻，這個 session 還在嗎？**不在就排程。

## 前置檢查

排工前先確認已安裝（launchd agent + `action` CLI）：

```bash
launchctl list | grep com.luhung.action   # agent 是否載入
command -v action || ls ~/.claude/action/bin/action.py
```

沒裝就提示使用者到 repo 跑 `./install.sh`（在 `action/` 目錄）。CLI 可能在 `~/.local/bin/action`，否則用 `python3 ~/.claude/action/bin/action.py`。

## 怎麼排工

把使用者的需求轉成一行 `action add`：

```bash
action add --at <時間> --cwd <專案目錄> --label <短標籤> "<自足的任務 prompt>"
```

- **`--at`**：絕對時鐘時間。接受 `3am` / `3:30pm` / `15:00` / `9:05am`，或帶日期的 `'2026-06-29 02:30'`。
  不帶日期時自動取**下一次**出現（今天還沒到就今天，過了就明天）。**不接受相對時間**——要排「15 分鐘後」得自己換算，例如 `--at "$(date -v+15M '+%H:%M')"`。
- **`--cwd`**：任務要在哪個專案目錄跑（**務必指定**，預設才不會落在錯的地方）。通常是目前的工作目錄。
- **`--label`**：通知與 `list` 用的短標籤。
- **prompt**：要寫成**自足**的——到點時是全新的 headless session，沒有現在這段對話的上下文。也可用 stdin 餵長 prompt。照這個樣板寫，四項齊全才算自足：
  ```
  目標：<做什麼、為什麼>
  範圍：<要動的檔案／目錄路徑；「只動這些」>
  完成判準：<跑哪個指令、看到什麼結果才算完成>
  禁止：<不可動的檔案、不可做的事（如：不要 push、不要改測試）>
  ```
- 選用：`--mode`（預設 `bypassPermissions` 無人值守；要安全可給 `acceptEdits`）、`--model`（如 `sonnet`/`haiku` 省成本）。

## 排完一定要驗證

`action add` 印出的訊息只證明指令跑過，不證明 job 真的躺在 queue 裡。**跑完 `action add` 必須接著跑 `action list`，並把該 job 那一行原樣貼給使用者**，才算完成排程；`list` 裡看不到就是沒排成功，據實說出來並重排。

在 queue 出現那一行之前，不准回報「已排程」。這條存在的理由是：宣稱排了卻其實沒排，在事後**完全查不出來**——沒進 queue 就不會留下任何失敗紀錄，使用者要到隔天才會發現，而那時錢已經燒完了。

回報內容：job id、預計啟動時間（CLI 會印 `~N min`）、跑的目錄與權限模式，以及 `action list` 的那一行。

## 樣板：遠端實例「看完就收」

雲端 GPU 按小時計費，這是 action 最值錢的用途。關鍵是**別排單次檢查**——單次檢查只在你猜對完成時間時有用，猜早了什麼都沒有，猜晚了就白燒。讓 job **自己續排**：到點檢查，沒跑完就排下一次檢查。

```bash
action add --at 07:00 --cwd <專案目錄> --label watch-<實例名> "$(cat <<'EOF'
目標：監看遠端實例 <實例名> 上的 <工作> 是否完成；完成就把結果安全拉回本地、
逐檔驗證，確認無誤後銷毀實例停止計費（每小時 $<費率>）。

範圍：遠端 <host> 的 <遠端目錄>（唯讀）；本地落地區 <本地目錄>。除此之外不要動任何檔案。

執行步驟：
1. ssh -o ConnectTimeout=30 <host> 檢查完成標記與進程狀態。
2. 若「尚未完成」：不要銷毀、不要拉檔。改為重新排下一次檢查後結束——
   action add --at "$(date -v+20M '+%H:%M')" --cwd <專案目錄> --label watch-<實例名> "<把這份 prompt 原樣再排一次>"
   排完務必跑 action list 確認新 job 在 queue 裡。
3. 若「已完成」：把產出全部拉回本地，逐檔比對 md5 與列數／大小。
4. 完成判準：每一個檔案的遠端與本地 md5 逐位吻合，且清點過遠端沒有未拉回的產出。
5. 只有在第 4 步全數通過後，才銷毀實例。

禁止：驗證未通過就銷毀實例。連不上、狀態判斷不出來、或發現有檔案沒拉回時，
一律保留實例並排下一次檢查——資料重跑的代價遠高於多燒幾小時的機器費。
EOF
)"
```

兩個要在 prompt 裡講死的原則：**驗證通過才銷毀**（銷毀不可逆，資料完整性優先於省錢），以及**狀態不明就續排而非放棄**（放棄等於無限期計費）。

## 管理

```bash
action list            # 待跑的 job
action list --all      # 連同 done / failed
action rm <id>         # 取消某個 job
```

## 重要提醒（要主動告知使用者）

- **粒度約 5 分鐘**：launchd 每 5 分鐘掃一次 queue，所以「3:00 啟動」實際是 3:00 後的下一個掃描點（實測延遲 0–5 分鐘）。
- **通知不可靠**：完成通知走 `osascript`，權限沒開時會靜默失敗，程式碼也刻意吞掉例外。**不要叫使用者靠通知橫幅判斷有沒有跑**——要查就查 `action list --all`、`~/.claude/action/logs/runner.log` 與 `logs/runs/<id>.log`。
- **睡眠**：Mac 睡著時 agent 跟著睡，醒來補跑——排在半夜要機器醒著（或設定喚醒）。
- **序列化**：多個 job 同時到點會**一個接一個**跑（一個長 job 會擋住後面的）。這是刻意的，避免同時併發燒掉多個 window。
- **無人值守風險**：預設 `bypassPermissions` 會讓 job 不經確認直接動檔案／跑指令。只排自己信得過、能寫成自足 brief 的機械性工作；觸及敏感操作就改 `--mode acceptEdits` 或別排。
- 不是 cron：只跑一次。要真正的週期性請用 `/schedule` 或 `/loop`；但**跨越 session 的一次性監看仍然屬於 action**，用上面的自我續排樣板。
