#!/usr/bin/env bash
# UserPromptSubmit hook: when a prompt has a *dispatch-worthy shape* (bulk /
# parallel / mechanical), force Claude to emit an explicit DISPATCH-or-SELF
# verdict before touching the work, instead of silently just-doing-it.
#
# A hook can only inject text; it can't make the model call the Skill. So the
# lever is salience: requiring a written verdict makes the model actually run
# the auto-dispatch policy (~/.claude/dispatch-policy.md) rather than skip it.
# Triggers on the dispatch-worthy *shape* (scope / plurality / known mechanical
# task types) — not on every coding prompt — so single small edits stay quiet.
input=$(cat)
prompt=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("prompt",""))' 2>/dev/null)

# Scope, plurality/parallel, and mechanical-task signals (zh + en).
shape='所有|每個|每一|整個|全部|各個|逐一|逐個|批次|大量|這幾個|那幾個|這些|那些|多個|幾個|兩個|三個|數個|跨檔|跨檔案|跨模組|平行|同時處理|分別|各自|docstring|註解|boilerplate|樣板|格式化|重構|重新命名|批次修|補測試|加測試|測試骨架'
shape_en='\b(all|every|each|both|several|multiple|across|bulk)\b|refactor|docstrings?|boilerplate|format(ting)?|rename|migrat(e|ion)|scaffold|every (file|module|test)|all (the )?(files|modules|tests)'

if printf '%s' "$prompt" | grep -qiE "$shape|$shape_en"; then
  cat <<'EOF'
[自動派工檢查] 此 prompt 具批次／平行／機械特徵。動手寫任何 code 前，先輸出一行裁決（二選一）：
  🚚 DISPATCH → <codex|gemini|opencode>，因為 <理由> ── 然後照 dispatch skill 發工、驗 diff、merge。
  🔨 SELF → 因為 <理由>（太小／需本對話上下文／需設計判斷／碰安全敏感碼）。
裁決標準見 ~/.claude/dispatch-policy.md（機械性或可平行＋能寫成自足 brief＋夠大或 2+ 獨立子任務 → 才派）。
沒先輸出裁決就直接自己動手 ＝ 違反政策。
EOF
fi
exit 0
