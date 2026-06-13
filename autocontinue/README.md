# autocontinue

Claude Code 撞到 usage limit 時，在額度 reset 後自動接續原任務。全自動、零操作：裝一次之後所有 session 都適用。

## 運作方式

```
claude session 撞 limit
  └─ StopFailure hook (matcher: rate_limit)
       └─ 寫入佇列 ~/.claude/autocontinue/queue/<root_id>.json + macOS 通知
launchd agent（每 5 分鐘；Mac 睡著就跟著睡，醒來補跑）
  └─ checker：全域 lock 序列化，依中斷順序檢查佇列
       └─ reset 時間已過 → claude --resume <session_id> -p "繼續…"
            ├─ 沿用原 session 的 permission mode（不升權）
            ├─ 再撞 limit → hook 重新入列（經 AUTOCONTINUE_ROOT 累計同一條鏈）
            ├─ 正常結束 → 移到 logs/done/
            └─ 連鎖達上限（預設 10 次）→ 放棄 + 通知
```

設計決策：headless resume 同一 session（原 TUI 會 stale，回來可 `claude --resume` 接手）、不
用 caffeinate（睡眠期間零進度，醒來補跑）、撞 limit／復活／放棄三事件都發通知。

## 安裝 / 移除

```bash
./install.sh     # 複製腳本、註冊 hook 到 ~/.claude/settings.json、載入 launchd agent
./uninstall.sh   # 反向移除（保留佇列與 log 資料）
```

更新腳本後重跑 `install.sh` 即可。

## 檔案位置

| 路徑 | 用途 |
|---|---|
| `~/.claude/autocontinue/queue/` | 待復活佇列（每 session 一檔） |
| `~/.claude/autocontinue/config.json` | 可調參數：`max_attempts`、`min_retry_wait_sec`、`resume_buffer_sec`、`resume_prompt`、`notify` |
| `~/.claude/autocontinue/logs/sessions/` | 每條鏈的 claude 輸出 |
| `~/.claude/autocontinue/logs/hook.log`、`checker.log` | 事件紀錄 |
| `~/.claude/autocontinue/logs/stopfailure-raw.jsonl` | StopFailure 原始 payload（校準解析用） |
| `~/.claude/autocontinue/logs/done/`、`dead/` | 完成／放棄的紀錄 |

## 已知限制與校準

- StopFailure payload 實測欄位：error kind 在 `error`（值 `rate_limit`），可讀訊息（含 reset
  時間）在 `last_assistant_message`，格式如 `You've hit your session limit · resets 2:50am
  (Asia/Taipei)`；hook 同時相容假設過的 `error_type` / `error_message` 欄名。解析器支援
  `|<epoch>`、ISO 時間、`resets 2:50am` 等格式；解析不到就退化成「中斷後每 15 分鐘嘗試一次」
  （失敗會被 hook 重新入列並計入停損）。新版 Claude Code 若改格式，看 `stopfailure-raw.jsonl`
  比對、必要時補 pattern。
- default 權限模式的 session 復活後，遇到第一個需要授權的工具就會停（headless 下無人可按
  允許）——這是「不自動升權」的刻意取捨。要無人值守跑完，發任務時就用
  `--dangerously-skip-permissions` 或 acceptEdits。
- checker 對 resume run 不設 timeout：長任務可跑數小時，期間其他待復活 session 會排隊
  （序列化是刻意設計）。若懷疑卡死，看 `logs/sessions/` 對應 log。

## 疑難排解

```bash
launchctl list | grep autocontinue          # agent 是否載入
cat ~/.claude/autocontinue/queue/*.json     # 目前佇列
tail ~/.claude/autocontinue/logs/checker.log
/usr/bin/python3 ~/.claude/autocontinue/bin/autocontinue_checker.py  # 手動跑一輪
```
