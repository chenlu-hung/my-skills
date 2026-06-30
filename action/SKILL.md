---
name: action
description: Schedule a Claude Code task to start at a specific wall-clock time and run headless/unattended, so heavy or batch work runs in an off-peak window and spreads load across the rolling usage limits. Use when the user says "排程", "幾點才開始跑", "幾點再執行", "延後到…執行", "離峰時段跑", "分攤 usage limit/額度", "schedule this to start at 3am", "run this later at …", "defer until …", or invokes "/action". NOT for recurring cron-style jobs (use /schedule or /loop) or for resuming after a limit is already hit (that is autocontinue).
---

# action — 排程在指定時間才啟動的 headless 任務

把一個任務 park 起來，到你指定的**絕對時鐘時間**才用 `claude -p` 在背景 headless 跑（預設無人值守、skip permissions）。用來把吃額度的粗重 / 批次工作挪到離峰或新 usage window，平均分攤 limit。

這是 `autocontinue` 的**主動版**：autocontinue 是撞到 limit 後等 reset 自動接續（被動）；action 是事先排到指定時間才開始（主動）。兩者可疊加——排程跑的 job 若中途撞 limit，全域安裝的 autocontinue StopFailure hook 會接手復活。

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
  不帶日期時自動取**下一次**出現（今天還沒到就今天，過了就明天）。
- **`--cwd`**：任務要在哪個專案目錄跑（**務必指定**，預設才不會落在錯的地方）。通常是目前的工作目錄。
- **`--label`**：通知與 `list` 用的短標籤。
- **prompt**：要寫成**自足**的——到點時是全新的 headless session，沒有現在這段對話的上下文。把目標、檔案路徑、完成判準都寫進去。也可用 stdin 餵長 prompt。
- 選用：`--mode`（預設 `bypassPermissions` 無人值守；要安全可給 `acceptEdits`）、`--model`（如 `sonnet`/`haiku` 省成本）。

排完**據實回報**：job id、預計啟動時間（CLI 會印 `~N min`）、跑的目錄與權限模式。

## 管理

```bash
action list            # 待跑的 job
action list --all      # 連同 done / failed
action rm <id>         # 取消某個 job
```

## 重要提醒（要主動告知使用者）

- **粒度約 5 分鐘**：launchd 每 5 分鐘掃一次 queue，所以「3:00 啟動」實際是 3:00 後的下一個掃描點。
- **睡眠**：Mac 睡著時 agent 跟著睡，醒來補跑——排在半夜要機器醒著（或設定喚醒）。
- **序列化**：多個 job 同時到點會**一個接一個**跑（一個長 job 會擋住後面的）。這是刻意的，避免同時併發燒掉多個 window。
- **無人值守風險**：預設 `bypassPermissions` 會讓 job 不經確認直接動檔案／跑指令。只排自己信得過、能寫成自足 brief 的機械性工作；觸及敏感操作就改 `--mode acceptEdits` 或別排。
- 不是 cron：只跑一次。要週期性請用 `/schedule` 或 `/loop`。
