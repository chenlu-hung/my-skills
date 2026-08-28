#!/usr/bin/env python3
"""Ask the ChatGPT desktop app one question over the Chrome DevTools Protocol.

Drives the real ChatGPT.app UI, so answers come out of the ChatGPT conversation
allowance rather than the Codex quota that `codex exec` spends.

The app must already be running with a debugging port open:

    osascript -e 'quit app id "com.openai.codex"'
    "/Applications/ChatGPT.app/Contents/MacOS/ChatGPT" --remote-debugging-port=9222 &

That port has no authentication -- anything on this machine can read the
conversation and post as the signed-in user -- so open it only while in use.

    python3 chatgpt_ask.py --prompt "..." [--timeout 300] [--port 9222]
    python3 chatgpt_ask.py --prompt-file q.txt --json

Exit status is 0 on a captured answer, 1 otherwise. `--json` prints
{"ok", "answer", "elapsed_s", "error"} for programmatic callers.
"""
import argparse
import asyncio
import json
import sys
import time
import urllib.error
import urllib.request

try:
    import websockets
except ImportError:
    sys.exit("missing dependency: pip install websockets")

# The composer's send control is labelled in the app's UI language; the stop
# control replaces it while a response streams. Both lists are matched
# case-insensitively, with a positional fallback when neither hits.
SEND_LABELS = {"傳送", "send", "skicka", "送信", "senden", "envoyer", "enviar", "отправить"}
STOP_LABELS = {"停止", "stop", "stoppa", "停止する", "arrêter", "detener"}

# The sidebar's own new-chat button carries no aria-label; the per-project ones
# do ("在 <project> 中開始新對話"), and starting a thread inside someone's
# project would file these questions under it. Matching on a bare label is what
# keeps this on the plain new chat.
NEW_CHAT_LABELS = {"新對話", "新聊天", "new chat", "ny chatt", "新しいチャット", "neuer chat"}

EDITOR = "document.querySelector('[contenteditable=\"true\"]')"

# Message bodies render into CSS-module roots whose class carries a build hash;
# only the readable prefix is stable. User turns sit inside a bubble marked with
# a semantic attribute, so anything outside one is a model turn.
MARKDOWN_ROOTS = "[class*=\"_MarkdownRoot_\"]"
USER_BUBBLE = "[data-user-message-bubble]"

COMPOSER_LABELS = """(() => {
  const f = document.querySelector('form') || document;
  return [...f.querySelectorAll('button')].map(b => b.getAttribute('aria-label') || '');
})()"""

# innerText is off-limits here: it returns only *rendered* text, so the moment the
# window is backgrounded or occluded every message reads as an empty string while
# the DOM still holds the answer. Rebuilding from block elements keeps paragraph
# and list breaks without depending on whether the window happens to be visible.
TRANSCRIPT = f"""(() => {{
  const text = (root) => {{
    const blocks = [...root.children];
    if (!blocks.length) return (root.textContent || '').trim();
    const render = (el) => {{
      const tag = el.tagName;
      if (tag === 'UL' || tag === 'OL') {{
        return [...el.children]
          .map((li, i) => (tag === 'OL' ? (i + 1) + '. ' : '- ') + (li.textContent || '').trim())
          .join('\\n');
      }}
      if (tag === 'PRE') return '```\\n' + (el.textContent || '').replace(/\\s+$/, '') + '\\n```';
      return (el.textContent || '').trim();
    }};
    return blocks.map(render).filter(Boolean).join('\\n\\n');
  }};
  const roots = [...document.querySelectorAll('{MARKDOWN_ROOTS}')];
  const asst = roots.filter(r => !r.closest('{USER_BUBBLE}'));
  const user = roots.filter(r =>  r.closest('{USER_BUBBLE}'));
  const tail = (list) => list.length ? text(list[list.length - 1]) : '';
  return JSON.stringify({{ user: tail(user), answer: tail(asst), turns: asst.length }});
}})()"""


class CDPError(RuntimeError):
    pass


class Page:
    """One CDP page target."""

    def __init__(self, ws):
        self.ws = ws
        self._id = 0

    async def call(self, method, params=None, timeout=30):
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise CDPError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr, timeout=30):
        res = await self.call("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
        }, timeout)
        if "exceptionDetails" in res:
            raise CDPError("JS: " + json.dumps(res["exceptionDetails"])[:200])
        return res.get("result", {}).get("value")


def pick_target(port):
    """The main window is the page target that owns the composer."""
    url = f"http://127.0.0.1:{port}/json/list"
    try:
        with urllib.request.urlopen(url, timeout=6) as r:
            targets = json.load(r)
    except (urllib.error.URLError, OSError) as exc:
        raise CDPError(
            f"no debugging port on {port} -- start ChatGPT.app with "
            f"--remote-debugging-port={port} ({exc})"
        ) from exc
    return [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]


async def find_composer(port):
    """Connect to whichever page target actually has a composer in it."""
    last = None
    for target in pick_target(port):
        ws = await websockets.connect(target["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024)
        page = Page(ws)
        try:
            if await page.eval(f"!!{EDITOR}"):
                return page, ws
        except CDPError as exc:
            last = exc
        await ws.close()
    raise CDPError(f"no page target has a composer{f' ({last})' if last else ''}")


async def click_labelled(page, wanted):
    """Click the composer button carrying one of `wanted`; report which matched."""
    labels = await page.eval(COMPOSER_LABELS) or []
    match = next((l for l in labels if l.strip().lower() in wanted or l.strip() in wanted), None)
    if match is None:
        return None
    await page.eval(
        "[...document.querySelector('form').querySelectorAll('button')]"
        f".find(b => (b.getAttribute('aria-label') || '') === {json.dumps(match)}).click(); true"
    )
    return match


async def has_label(page, wanted):
    labels = await page.eval(COMPOSER_LABELS) or []
    return any(l.strip().lower() in wanted or l.strip() in wanted for l in labels)


def normalize(text):
    return " ".join((text or "").split())


async def transcript(page):
    raw = await page.eval(TRANSCRIPT)
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"user": "", "answer": "", "turns": 0}


async def start_new_chat(page):
    wanted = json.dumps(sorted(NEW_CHAT_LABELS))
    clicked = await page.eval(f"""(() => {{
      const wanted = {wanted};
      const btn = [...document.querySelectorAll('button')].find(b =>
        !b.getAttribute('aria-label') &&
        wanted.includes((b.innerText || '').trim().toLowerCase()));
      if (!btn) return false;
      btn.click();
      return true;
    }})()""")
    if clicked:
        await asyncio.sleep(1.2)
    return bool(clicked)


async def ask(prompt, timeout, port, new_chat=True, settle=1.5, poll=0.7):
    page, ws = await find_composer(port)
    started = time.time()
    try:
        if new_chat:
            await start_new_chat(page)

        # Anchor on the question itself rather than on a turn count: switching
        # threads clears the transcript asynchronously, so any count read before
        # the clear lands is stale, and a stale baseline can never be beaten.
        baseline = normalize((await transcript(page))["answer"])
        anchor = normalize(prompt)[:80]

        await page.eval(f"{EDITOR}.focus(); true")
        # Injecting at the input layer is what makes ProseMirror update its own
        # state; assigning innerText leaves the editor model empty and the send
        # button never appears.
        await page.call("Input.insertText", {"text": prompt})
        await asyncio.sleep(0.6)

        if not await click_labelled(page, SEND_LABELS):
            raise CDPError("send button never appeared -- the composer stayed empty")

        # Done means: our turn is in the transcript, the stop control is gone,
        # and the answer has not changed for `settle` seconds. Generation pauses
        # mid-answer, so text stability alone would cut long replies short.
        stable_since = None
        last = None
        answer = ""
        deadline = started + timeout
        while time.time() < deadline:
            await asyncio.sleep(poll)
            state = await transcript(page)
            answer = state["answer"]
            current = normalize(answer)

            posted = bool(anchor) and anchor in normalize(state["user"])
            if not posted and current == baseline:
                continue
            if await has_label(page, STOP_LABELS):
                last, stable_since = current, None
                continue
            if current != last:
                last, stable_since = current, time.time()
                continue
            if current and stable_since and time.time() - stable_since >= settle:
                return {
                    "ok": True, "answer": answer.strip(),
                    "elapsed_s": round(time.time() - started, 1), "error": "",
                }
            if stable_since is None:
                stable_since = time.time()

        return {
            "ok": False, "answer": answer.strip(),
            "elapsed_s": round(time.time() - started, 1),
            "error": f"timed out after {timeout}s",
        }
    finally:
        await ws.close()


def main():
    ap = argparse.ArgumentParser(description="Ask ChatGPT.app one question over CDP.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--prompt")
    src.add_argument("--prompt-file")
    ap.add_argument("--timeout", type=int, default=300, help="seconds to wait for the answer")
    ap.add_argument("--port", type=int, default=9222, help="CDP port ChatGPT.app listens on")
    ap.add_argument("--json", action="store_true", help="emit the full result object")
    ap.add_argument("--keep-chat", action="store_true",
                    help="ask in the open thread instead of starting a new one")
    args = ap.parse_args()

    prompt = args.prompt
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as fh:
            prompt = fh.read()
    prompt = (prompt or "").strip()
    if not prompt:
        sys.exit("empty prompt")

    try:
        result = asyncio.run(ask(prompt, args.timeout, args.port, new_chat=not args.keep_chat))
    except CDPError as exc:
        result = {"ok": False, "answer": "", "elapsed_s": 0, "error": str(exc)}

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif result["ok"]:
        print(result["answer"])
    else:
        print(result["error"], file=sys.stderr)
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
