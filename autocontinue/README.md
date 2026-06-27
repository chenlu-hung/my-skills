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

## 省成本

reset 常在數小時後才發生，那時 prompt cache 早已過期（預設 5 分鐘、最長 1 小時），所以
`claude --resume` 會把整份 transcript 以**全價 input** 重讀一次；連鎖復活時這份歷史還會越滾越大。
兩個可調的槓桿（都在 `config.json`）：

- **`resume_model`**（預設 `null`）：設成 `"sonnet"`／`"haiku"`／某 model id，復活時用較便宜的 model
  重讀 transcript。單價直接從 Opus 的 $5/1M 降到 Sonnet $3 或 Haiku $1，每次復活都生效、零結構風險，
  代價是續跑品質降一級——對「把剩下的機械工作收尾」通常無感。
- **`resume_mode`**（預設 `"session"`）：改成 `"handoff"` 後，**第一次**復活不 resume 原 session，而是
  開一個全新 session，只塞一段指向原始 transcript 的 seed prompt（`handoff_prompt`，含 `{transcript_path}`）。
  新 session 預設只讀 transcript 結尾來定位，需要某個決策的「為什麼」時才針對性 grep——只付真正用到那片的錢。
  之後這個（小）session 若再撞 limit，就照常 `--resume`（此時它 context 很小、便宜又無損）。
  代價：新 session 只知道它從 transcript 讀回來的東西，保真度不如 resume 同一 session。

兩者可疊加（handoff 的新 session 也跑 `resume_model`）。改完 `config.json` 直接生效，不必重裝。

## 就地接續（`inject` 模式，kitty 限定）

`resume_mode` 還有第三個值 **`"inject"`**：reset 後**不**自己在背景 headless 跑 claude，而是把 `resume_prompt`
直接「打」回你原本盯著的那個 **kitty 視窗**的 Claude TUI，讓接續**可見、就地**發生，你隨時能接手。

```
checker 偵測到 reset 已過
  └─ entry 有 kitty_listen_on + kitty_window_id 且該視窗仍開著（kitty @ ls 確認）
       └─ kitty @ send-text <resume_prompt>   ← 把字打進輸入框
          kitty @ send-key  enter             ← 獨立的 Enter 事件才會送出
```

重點機制與前提：

- StopFailure hook 跑在被限流 session 的 process tree 裡，kitty 早已把 `KITTY_WINDOW_ID` + `KITTY_LISTEN_ON`
  匯出到環境，hook 把兩者存進 queue entry；checker 之後就靠 `kitty @ --to <listen>` 走 kitty 的 unix socket
  鎖定那個視窗（不碰 AppleScript／TCC）。
- **提交一定要用獨立的 `send-key enter`**，不能把 `\r` 黏在 `send-text` 文字尾端：那串會被當成一次 paste，
  Claude Code 的 Ink TUI 把 paste 裡的換行當「插入一行」而非「按 Enter」，prompt 會卡在輸入框不送出——
  而 `send-text` 照樣回 `rc=0`，checker 會誤記成功。所以實作是先 `send-text` 文字、停 0.4 秒、再 `send-key enter`。
- **前提**：`~/.config/kitty/kitty.conf` 要有 `allow_remote_control socket-only` 與 `listen_on unix:/tmp/kitty`，
  而且**只有重啟 kitty 之後新開的視窗**才帶 `KITTY_LISTEN_ON`。沒有遠端控制、視窗已關、或在別的終端機開的
  session → `can_inject` 失敗 → checker **自動退回 headless `session` 復活**，工作不會掉。
- inject 跑在你的 TUI 裡，checker 看不到它的結束。再撞 limit 由 hook 把 entry 翻回 `waiting`（連鎖／停損照舊）；
  正常跑完則 entry 停在 `injected`，到 `inject_ttl_sec`（預設 6 小時）才退役到 `done/`。
- inject 模式下 `resume_model` 不適用（不是我們在起 claude）；要省成本請搭 `session`／`handoff`。

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
| `~/.claude/autocontinue/config.json` | 可調參數：`max_attempts`、`min_retry_wait_sec`、`resume_buffer_sec`、`resume_prompt`、`resume_model`、`resume_mode`、`handoff_prompt`、`kitty_bin`、`inject_ttl_sec`、`notify` |
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
- `claude --resume <id>` 只認「啟動目錄」那個 project 底下的 session id；payload 的 `cwd` 可能是
  子目錄（如某 skill 在 `.claude/app` 裡工作），直接拿它復活會落到別的 project、報
  `No conversation found`。所以復活目錄改由 `transcript_path` 反推：從 `cwd` 往上找第一個
  「編碼後（非英數字一律轉 `-`）等於 transcript project 目錄名」的祖先。解析不到才退回 `cwd`。
- default 權限模式的 session 復活後，遇到第一個需要授權的工具就會停（headless 下無人可按
  允許）——這是「不自動升權」的刻意取捨。要無人值守跑完，發任務時就用
  `--dangerously-skip-permissions` 或 acceptEdits。
- handoff 模式（`resume_mode: "handoff"`）的新 session 要讀原始 transcript 才能定位，而 transcript
  在 `~/.claude/projects/` 底下、通常在工作目錄之外——default 權限模式會卡在那個 Read，所以
  handoff 同樣需要 acceptEdits／bypass 才能無人值守跑。另外它指向的是原始 transcript 檔，
  autocontinue 不會去刪它（只搬佇列 entry 到 done／dead），但若你自行清掉 `~/.claude/projects/`
  下的舊 transcript，handoff 的 grep 回溯就會失效。
- checker 對 resume run 不設 timeout：長任務可跑數小時，期間其他待復活 session 會排隊
  （序列化是刻意設計）。若懷疑卡死，看 `logs/sessions/` 對應 log。
- `inject` 模式靠 kitty 遠端控制，限 kitty、且該視窗要在重啟 kitty 後新開（才有 `KITTY_LISTEN_ON`）。
  注入只是把 `resume_prompt` 打進那個 TUI，所以那條 session 的權限模式仍由它自己決定（與 headless
  復活的「不升權」無關）。提交用獨立的 `send-key enter`——若日後 kitty 改了 send-key 語法、或 Claude Code
  改了 paste／Enter 的處理，注入可能又卡在輸入框不送出：對照 `checker.log` 的 `injected ... (rc=0)` 與
  該視窗 `kitty @ get-text` 的畫面即可確認。前提不滿足時 checker 一律退回 `session` 復活。

## 疑難排解

```bash
launchctl list | grep autocontinue          # agent 是否載入
cat ~/.claude/autocontinue/queue/*.json     # 目前佇列
tail ~/.claude/autocontinue/logs/checker.log
/usr/bin/python3 ~/.claude/autocontinue/bin/autocontinue_checker.py  # 手動跑一輪
```
