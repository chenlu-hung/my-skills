# action

把 Claude Code 任務排到指定的**絕對時鐘時間**才 headless 啟動，平均分攤 usage limit。是 [`autocontinue`](../autocontinue) 的主動版：autocontinue 撞到 limit 後等 reset 自動接續（被動），action 事先把粗重 / 批次工作排到離峰或新 usage window 才開始（主動）。

## 運作方式

```
action add --at 3am --cwd ~/proj "<自足的任務 prompt>"
  └─ 寫入佇列 ~/.claude/action/queue/<id>.json（status=scheduled, start_at=epoch）
launchd agent（每 5 分鐘；Mac 睡著就跟著睡，醒來補跑）
  └─ runner：全域 lock 序列化，掃出 start_at 已過的 job
       └─ claude -p "<prompt>" --dangerously-skip-permissions（在 job 的 cwd）
            ├─ rc=0  → 移到 logs/done/ + 通知
            ├─ rc≠0  → 移到 logs/dead/ + 通知（若是撞 limit，autocontinue 另行接手）
            └─ 多個同時到點 → 依 start_at 先後一個接一個跑（不併發）
```

## 設計取捨

幾個刻意的決定，先講清楚免得踩到：

- **headless 一次性啟動，不是讓 session 空等**：到點才起 `claude -p`，排程期間不佔任何 session；不用 caffeinate（睡眠零進度、醒來補跑）。
- **預設 `bypassPermissions`**：半夜沒人按「允許」，不 skip 就會卡在第一個需授權的工具。代價是 job 會不經確認動檔案／跑指令——只排信得過、能寫成自足 brief 的機械性工作，敏感操作改 `--mode acceptEdits` 或別排。
- **序列化、不併發**：多個 job 同時到點會依 `start_at` 一個接一個跑。一個長 job 會擋住後面的——這是刻意的，避免同時併發反而燒掉多個 window，違背「分攤額度」的初衷。
- **粒度約 5 分鐘 + 睡眠跟著睡**：launchd 每 5 分鐘輪詢，「3:00 啟動」實際是 3:00 後的下一個掃描點；Mac 睡著 agent 也睡，排半夜要讓機器醒著（或設定喚醒）。換到精準到秒的 `StartCalendarInterval` 一次性 plist 會讓每個 job 都要管自己的 plist，複雜度不划算，故用輪詢。
- **跟 autocontinue 分工疊加**：見下節。action 管「何時開始」，autocontinue 管「中斷後接續」；保真度與停損都交給 autocontinue，action 自己只記錄一次啟動的結果。

## 跟 autocontinue 疊加

排程跑的 job 是一般的 `claude -p` run，所以全域安裝的 autocontinue StopFailure hook 照樣生效：job 中途撞 limit → autocontinue 排隊、reset 後復活續跑。action 負責「何時開始」，autocontinue 負責「中斷後接續」。要無人值守整段跑完，兩個都裝。

## 用法

```bash
action add --at 3am --cwd ~/proj --label nightly "把剩下的 docstring 補完並跑測試"
action add --at "2026-06-29 02:30" --label report "跑年度報表並輸出到 out/"
action add --at 15:00 --model sonnet "整理 changelog"   # 便宜 model 跑
action list            # 待跑
action list --all      # 連同 done / failed
action rm <id>         # 取消
```

`--at` 接受 `3am` / `3:30pm` / `15:00` / `9:05am`，或帶日期的 `'YYYY-MM-DD HH:MM'`。不帶日期取下一次出現。

## 安裝 / 移除

```bash
./install.sh     # 複製腳本、寫 config、裝 action wrapper、載入 launchd agent
./uninstall.sh   # 反向移除（保留佇列與 log）
```

更新腳本後重跑 `install.sh`。CLI wrapper 會裝到 `~/.local/bin/action`（若該目錄存在）；否則用 `python3 ~/.claude/action/bin/action.py`。

## 注意事項

（粒度／睡眠／無人值守風險見上面「設計取捨」。）

- **prompt 要自足**：到點時是全新 headless session，沒有任何當前對話的上下文——把目標、檔案路徑、完成判準寫進 prompt。
- **只跑一次**：要週期性用 Claude Code 的 `/schedule` 或 `/loop`。

## 檔案位置

| 路徑 | 用途 |
|---|---|
| `~/.claude/action/queue/` | 待跑佇列（每 job 一檔） |
| `~/.claude/action/config.json` | `claude_bin`、`permission_mode`、`model`、`notify` |
| `~/.claude/action/logs/runs/` | 每個 job 的 claude 輸出 |
| `~/.claude/action/logs/runner.log` | runner 事件 |
| `~/.claude/action/logs/done/`、`dead/` | 完成 / 異常的 job |

## 疑難排解

```bash
launchctl list | grep com.luhung.action                       # agent 是否載入
cat ~/.claude/action/queue/*.json                             # 目前佇列
tail ~/.claude/action/logs/runner.log
/usr/bin/python3 ~/.claude/action/bin/action_runner.py        # 手動掃一輪
```
