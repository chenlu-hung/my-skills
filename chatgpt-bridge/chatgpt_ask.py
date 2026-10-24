#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["websockets>=12"]
# ///
"""Talk to the ChatGPT desktop app over the Chrome DevTools Protocol.

Drives the real ChatGPT.app UI, so answers come out of the ChatGPT conversation
allowance rather than the Codex quota that `codex exec` spends.

    chatgpt-ask --prompt "..."                        # one question, temporary chat
    chatgpt-ask --prompt-file q.txt --json --timeout 300
    chatgpt-ask --project "My research" --prompt-file q.txt   # new chat in a project
    chatgpt-ask --chat <id|url> --prompt-file q.txt           # continue a saved chat
    chatgpt-ask --read <id|url> > thread.md                   # transcript, nothing sent
    chatgpt-ask --list-project ["My research"]                # projects, or one's chats

The app is started and stopped for you. If nothing is listening on the port,
this relaunches ChatGPT.app with it open and quits the app again on the way
out; an app that was *already* serving the port is left running, because that
one belongs to the user's own session rather than to this call.

By default a question goes into a **temporary chat**, which is not written to
the account's history. That is deliberate: deleting a thread afterwards would
leave a window where a crash strands it, and would rely on the sidebar's
delete/confirm UI holding still across app updates. `--project` and `--chat`
are the explicit way out of that, for work that is meant to persist: they file
the question in the named project or thread and report its URL so the next
call can come back to it.

The debugging port has no authentication -- for as long as it is open, any
process on this machine can read the conversation and post as the signed-in
user. Hence opening it per call rather than leaving it on.

Exit status is 0 on success, 1 otherwise. `--json` prints a result object for
programmatic callers; for a question that is
{"ok", "answer", "elapsed_s", "error", "chat_url", "conversation_id",
 "project_id", "temporary", "skills"}.

Callers are usually agent CLIs -- Claude Code, Codex, opencode -- and two of
those hosts restrict what may be done to the app. `--doctor` reports what this
host allows and whether a call would get through.
"""
import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

try:
    import websockets
except ImportError:
    sys.exit("missing dependency `websockets` — run this with `uv run chatgpt_ask.py`, "
             "or with a Python that has it installed")

APP_PATH = "/Applications/ChatGPT.app"
APP_BINARY = os.path.join(APP_PATH, "Contents/MacOS/ChatGPT")
APP_BUNDLE_ID = "com.openai.codex"  # the merged ChatGPT/Codex desktop app
WEB_ORIGIN = "https://chatgpt.com"  # the app's routes are chatgpt.com's paths

# The composer's send control is labelled in the app's UI language; the stop
# control replaces it while a response streams. Send is matched exactly, stop
# by substring, both case-insensitively.
SEND_LABELS = {"傳送", "send", "skicka", "送信", "senden", "envoyer", "enviar", "отправить"}
STOP_LABELS = {"停止", "stop", "stoppa", "arrêter", "detener"}

# The temporary-chat toggle is a plain button whose label is the only thing that
# names it. Its wording has already changed under us once -- "開啟暫存對話" became
# "暫存對話", and a pattern keyed on the verb read the new label as "already on"
# and quietly asked in a saved chat. So the label is used only to *find* the
# button; whether temporary mode is on is read from the composer's own state.
TEMP_CHAT_PATTERN = r"暫存|temporary"

# The sidebar's own new-chat button carries no aria-label; the per-project ones
# do ("在 <project> 中開始新對話"), and starting a thread inside someone's
# project would file these questions under it. Matching on a bare label is what
# keeps a temporary question on the plain new chat.
NEW_CHAT_LABELS = {"新對話", "新聊天", "new chat", "ny chatt", "新しいチャット", "neuer chat"}

# What a non-temporary question is prefixed with. Everything typed through the
# bridge shows up in the thread as the signed-in user's own message, so without
# a marker the user cannot tell later which turns were theirs.
DEFAULT_TAG = "【Claude Code】"

UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
PROJECT_ID = r"g-p-[0-9a-f]{32}"

# --- Page-side helpers ----------------------------------------------------------
#
# Everything below reads the app through React's own props rather than through
# rendered text, for three reasons that each cost a wrong answer before:
#
# - Every thread opened in a session stays mounted, hidden. Selectors over the
#   whole document mix turns from several threads; only the *visible* timeline
#   and composer belong to the thread on screen.
# - The timeline is virtualized: only turns near the viewport are in the DOM, so
#   reading the DOM gives a window of the thread, not the thread. The turn list
#   component's `entries` prop holds all of it.
# - Answers render through several pipelines (plain markdown, the generated-UI
#   "DIL" renderer, deep-research widgets), and their text in the DOM loses math,
#   tables and links in different ways. The props carry the source markdown.
#
# Props are internal and can move under an app update; when they do, re-probe
# rather than guess. The README says how.
PRELUDE = r"""
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
const shown = (el) => !!el && el.checkVisibility();
const fiberOf = (el) => {
  if (!el) return null;
  const k = Object.keys(el).find(k => k.startsWith('__reactFiber'));
  return k ? el[k] : null;
};
const propsUp = (el, pred, limit = 400) => {
  for (let f = fiberOf(el), i = 0; f && i < limit; f = f.return, i++) {
    const p = f.memoizedProps;
    if (p && typeof p === 'object' && pred(p)) return p;
  }
  return null;
};
const propsDown = (el, pred, limit = 20000) => {
  const queue = [fiberOf(el)];
  for (let n = 0; queue.length && n < limit; n++) {
    const f = queue.shift();
    if (!f) continue;
    const p = f.memoizedProps;
    if (p && typeof p === 'object' && pred(p)) return p;
    for (let c = f.child; c; c = c.sibling) queue.push(c);
  }
  return null;
};
const router = () => {
  const p = propsUp(document.querySelector('main'), p => p.router && typeof p.router.navigate === 'function');
  return p ? p.router : null;
};
const route = () => { const r = router(); return r ? r.state.location.pathname : null; };
const composer = () => [...document.querySelectorAll('form[data-chatgpt-composer]')].find(shown) || null;
const editor = () => { const f = composer(); return f ? f.querySelector('[contenteditable="true"]') : null; };
const composerButtons = () => { const f = composer(); return f ? [...f.querySelectorAll('button')].filter(shown) : []; };
const labelOf = (b) => (b.getAttribute('aria-label') || '').trim();
const composerState = () => {
  const f = composer();
  const p = f && propsUp(f, p => 'conversationId' in p && 'projectId' in p && 'isTemporaryChat' in p, 60);
  return p ? {conversationId: p.conversationId, projectId: p.projectId, temporary: !!p.isTemporaryChat, route: route()} : null;
};
const timeline = () => [...document.querySelectorAll('[data-app-action-timeline-scroll]')].find(shown) || null;
const turnEntries = () => {
  const t = timeline();
  const p = t && propsDown(t, p => Array.isArray(p.entries) && p.entries.length > 0 && !!p.entries[0] && 'turn' in p.entries[0]);
  return p ? p.entries : [];
};
// The sidebar's project section also lists Codex workspaces (uuid ids); ChatGPT
// projects are the gizmos, g-p-<hex>.
const projectRows = () => [...document.querySelectorAll('[data-app-action-sidebar-project-row]')].map(r => ({
  id: r.getAttribute('data-app-action-sidebar-project-id'),
  name: r.getAttribute('data-app-action-sidebar-project-label'),
})).filter(r => /^g-p-/.test(r.id || ''));
const sidebarSource = () => {
  const box = document.querySelector('[data-sidebar-project-container-id]');
  const p = box && propsUp(box, p => p.chatGptSource && Array.isArray(p.chatGptSource.chatTargets));
  return p ? p.chatGptSource : null;
};
const titleOf = (id) => {
  const src = sidebarSource();
  const t = src && src.chatTargets.find(t => t.conversationId === id);
  if (t && t.conversation) return t.conversation.title;
  for (const el of document.querySelectorAll('[data-sidebar-cycle-item][role=button]')) {
    const p = propsUp(el, p => p.conversation && p.conversation.id === id, 15);
    if (p) return p.conversation.title;
  }
  return null;
};

// --- Turn -> markdown ---
//
// Citations arrive in one of two encodings, both resolved through the message's
// content references:
// - private-use-area tokens (U+E200 kind U+E202 payload U+E201), each the
//   `matched_text` of a reference -- the raw form, as in deep-research reports;
// - directives `:chatgpt-content-reference{index=N}` (inline) and
//   `::chatgpt-content-reference{index=N}` (block), indexing the references --
//   what the app has already turned the tokens of an ordinary answer into.
// A reference's `alt` is the markdown the app's own copy button substitutes. A
// generated-UI answer is a single block directive whose reference carries a
// markdown fallback of the whole rendering.
const TOKEN = /([^]*)/g;
const DIRECTIVE = /:{1,2}chatgpt-content-reference\{index="(\d+)"[^}]*\}/g;
const cleanUrls = (s) => (s || '').replace(/\?utm_source=chatgpt\.com&/g, '?').replace(/[?&]utm_source=chatgpt\.com/g, '');
const stripTokens = (s, fileNames) => (s || '').replace(TOKEN, (m, inner) => {
  const [kind, ...rest] = inner.split('');
  if (kind === 'entity') {
    try { const v = JSON.parse(rest.join('')); return Array.isArray(v) ? String(v[1] || '') : ''; } catch (e) { return ''; }
  }
  if (kind === 'filecite') {
    const name = fileNames && fileNames.get(String(rest[0] || '').trim());
    return name ? ' [file: ' + name + ']' : '';
  }
  return '';
}).replace(/cite(?:turn\d+[a-z_]+\d+)+/g, '');
const linkFor = (r) => {
  if (r.alt) return ' ' + r.alt.trim();
  const items = [...(r.url ? [r] : []), ...(r.items || []), ...(r.sources || [])].filter(i => i && i.url);
  const seen = new Set(), links = [];
  for (const i of items) {
    if (seen.has(i.url)) continue;
    seen.add(i.url);
    links.push('[' + String(i.attribution || i.title || 'source').replace(/[\[\]]/g, '') + '](' + i.url + ')');
  }
  return links.length ? ' (' + links.join(', ') + ')' : '';
};
const PUA = /[-]/;
const expandRef = (r, ctx) => {
  if (!r) return '';
  switch (r.type) {
    case 'dil':
      ctx.dil = true;
      return (r.model_dil_v2 && r.model_dil_v2.fallbackMarkdown) || '';
    case 'client_defined_widget':  // writing blocks
      return r.data && typeof r.data.content === 'string' ? r.data.content : '';
    case 'hidden':
    case 'sources_footnote':  // the footer source list; listed separately when needed
      return '';
    case 'file':  // literal text such as `CM03.pdf`, or a download link, unless it was a token
      return r.matched_text && !PUA.test(r.matched_text) ? r.matched_text : ' [file: ' + (r.name || r.id) + ']';
    default:
      return linkFor(r);
  }
};
const resolveText = (text, refs, ctx) => {
  let out = (text || '').replace(DIRECTIVE, (m, idx) => expandRef((refs || [])[Number(idx)], ctx));
  for (const r of refs || []) {
    const m = r && r.matched_text;
    if (!m || !PUA.test(m)) continue;
    const at = out.indexOf(m);
    if (at >= 0) out = out.slice(0, at) + expandRef(r, ctx) + out.slice(at + m.length);
  }
  return out;
};
const isResearch = (w) => /deep.?research/i.test(((w.metadata || {}).html_asset_pointer || '') + ' ' + ((w.metadata || {}).attribution_id || ''));
const researchReport = (w, ctx) => {
  let st = null;
  try { st = JSON.parse((w.metadata || {}).widget_state || 'null'); } catch (e) { return {status: 'unreadable', text: ''}; }
  const rm = st && st.report_message;
  if (!rm) return {status: (st && st.status) || 'running', text: ''};
  const parts = ((rm.content || {}).parts || []).filter(p => typeof p === 'string').join('\n\n');
  return {status: st.status, text: resolveText(parts, (rm.metadata || {}).content_references, ctx)};
};
const renderTurn = (entry) => {
  const turn = entry.turn || {};
  const ctx = {dil: false};
  let user = '', pending = false;
  const answer = [], files = [], footnotes = [], fileNames = new Map();
  for (const it of turn.items || []) {
    if (it.type === 'user-message') { user = it.message || ''; continue; }
    if (it.type === 'assistant-message') {
      const refs = it.contentReferences || [];
      answer.push(resolveText(typeof it.content === 'string' ? it.content : '', refs, ctx));
      if (it.completed === false) pending = true;
      for (const r of [...refs, ...(it.sidebarSourceReferences || [])]) {
        if (r.type === 'file' && r.name) {
          files.push({name: r.name, generated: /sandbox:/.test(r.matched_text || '')});
          if (r.id) fileNames.set(r.id, r.name);
        }
        if (r.type === 'sources_footnote')
          for (const s of (r.sources && r.sources.length ? r.sources : [r])) if (s.url) footnotes.push(s);
      }
      continue;
    }
    if (it.type === 'chatgpt-hosted-widget') {
      if (!isResearch(it)) { answer.push('[widget: ' + ((it.metadata || {}).resource_name || 'app') + ']'); continue; }
      const rep = researchReport(it, ctx);
      if (rep.status !== 'completed') pending = true;
      answer.push(rep.text);
    }
  }
  let md = stripTokens(answer.filter(s => s && s.trim()).join('\n\n'), fileNames);
  // A generated-UI answer's fallback drops its inline citations, so list them.
  if (ctx.dil && footnotes.length) {
    const seen = new Set();
    const lines = footnotes.filter(s => !seen.has(s.url) && seen.add(s.url))
      .map(s => '- [' + String(s.title || s.attribution || s.url).replace(/[\[\]]/g, '') + '](' + s.url + ')');
    md += '\n\n**Sources**\n\n' + lines.join('\n');
  }
  const names = new Set();
  return {
    user, answer: cleanUrls(md).trim(), status: turn.status || null, pending,
    skills: (turn.skillSources || []).map(s => s.label).filter(Boolean),
    files: files.filter(f => !names.has(f.name) && names.add(f.name)),
  };
};
"""


def script(body, args=None):
    return ("(async () => {\n" + PRELUDE + "\nconst ARGS = " + json.dumps(args or {})
            + ";\n" + body + "\n})()")


class CDPError(RuntimeError):
    pass


# Chromium binds the debugging port to whichever loopback stack it picks at
# launch, and it is not consistently IPv4: the same binary has come up on
# 127.0.0.1 one run and [::1] the next. Probing only one of them reports a live
# port as dead, so every lookup goes through resolve_host.
CDP_HOSTS = ("127.0.0.1", "[::1]")


def resolve_host(port, timeout=2):
    """The loopback host actually serving the debugging port, or None."""
    for host in CDP_HOSTS:
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/json/version", timeout=timeout):
                return host
        except (urllib.error.URLError, OSError):
            continue
    return None


def cdp_ready(port):
    return resolve_host(port) is not None


def app_is_running():
    return subprocess.run(["pgrep", "-x", "ChatGPT"],
                          capture_output=True).returncode == 0


def quit_app(wait=15):
    subprocess.run(["osascript", "-e", f'quit app id "{APP_BUNDLE_ID}"'],
                   capture_output=True, timeout=20)
    deadline = time.time() + wait
    while time.time() < deadline:
        if not app_is_running():
            return True
        time.sleep(0.4)
    return not app_is_running()


# --- Where this is running ----------------------------------------------------
#
# Calls arrive from several agent CLIs as well as from a plain shell, and two of
# those hosts turn an ordinary call into damage. Both are checked before the app
# is touched, because otherwise the user's app is quit and relaunched for a call
# that was never going to reach the port.

def ancestry(limit=16):
    """Executable paths of this process's ancestors, nearest first."""
    chain = []
    pid = os.getppid()
    while pid > 1 and len(chain) < limit:
        probe = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                               capture_output=True, text=True)
        parts = probe.stdout.strip().split(None, 1)
        if len(parts) < 2:
            break
        chain.append(parts[1].strip())
        try:
            pid = int(parts[0])
        except ValueError:
            break
    return chain


def host():
    """(label, may_manage_app, why_not) for the process tree this call sits in.

    Managing the app means quitting and relaunching it, which is the only way to
    open the debugging port -- the flag is read at launch and `open --args`
    against a running instance drops it. Two hosts must not do that:

    - ChatGPT.app itself. It and the Codex desktop app are one bundle
      (com.openai.codex), so a Codex session hosted in the app would quit the
      window it is running in. Ancestry is the only signal; nothing in the
      environment says so.
    - A Codex CLI sandbox. Children inherit the seatbelt profile, so an app
      relaunched from inside one comes up unable to write its own container.

    Under either, the port has to be opened outside and this call just uses it.
    """
    chain = ancestry()
    if any(p.startswith(APP_PATH + "/") for p in chain):
        return ("ChatGPT.app", False,
                "quitting it would kill the session making this call")
    if os.environ.get("CODEX_SANDBOX"):
        return ("Codex CLI (sandboxed)", False,
                "an app relaunched inside the sandbox inherits it and cannot "
                "write its own container")
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return ("Claude Code", True, "")
    for path in chain:
        name = os.path.basename(path).lstrip("-")
        if name in ("codex", "opencode"):
            return ({"codex": "Codex CLI", "opencode": "opencode"}[name], True, "")
    return ("shell", True, "")


def unreachable_reason():
    """Why nothing can work on this host at all, or None if something might."""
    if sys.platform != "darwin":
        return f"ChatGPT.app is macOS-only (this is {sys.platform})"
    if os.environ.get("CODEX_SANDBOX_NETWORK_DISABLED"):
        return ("the Codex sandbox has networking off, so the debugging port "
                "cannot be reached -- rerun codex with "
                "`--sandbox danger-full-access`, or call chatgpt-ask outside Codex")
    if not os.path.exists(APP_BINARY):
        return f"ChatGPT.app not found at {APP_PATH}"
    return None


OPEN_IT_YOURSELF = ("open the port from an unrestricted shell first -- "
                    "`chatgpt-ask --keep-app --list-project` leaves it open without "
                    "asking anything -- then retry here")


def ensure_app(port, wait=45):
    """Make the debugging port answer; report whether this call is what opened it.

    A port that is already answering belongs to the user's own session -- leave
    it alone, and leave it running afterwards. Otherwise the app has to be
    restarted, because the flag is only read at launch: `open --args` against a
    running instance silently drops it.
    """
    if cdp_ready(port):
        return False
    label, may_manage, why_not = host()
    if not may_manage:
        raise CDPError(
            f"nothing is serving port {port}, and under {label} this call must "
            f"not quit and relaunch the app ({why_not}) -- {OPEN_IT_YOURSELF}"
        )
    if not os.path.exists(APP_BINARY):
        raise CDPError(f"ChatGPT.app not found at {APP_PATH}")
    if app_is_running() and not quit_app():
        raise CDPError("ChatGPT.app would not quit, so the port cannot be opened")

    # Detached: the app has to outlive this process when --keep-app is used.
    subprocess.Popen(
        [APP_BINARY, f"--remote-debugging-port={port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, start_new_session=True,
    )
    deadline = time.time() + wait
    while time.time() < deadline:
        if cdp_ready(port):
            return True
        time.sleep(0.5)
    raise CDPError(f"ChatGPT.app did not open port {port} within {wait}s")


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
            detail = res["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise CDPError("JS: " + str(text)[:300])
        return res.get("result", {}).get("value")

    async def run(self, body, args=None, timeout=30):
        """Run `body` as an async function with the page-side helpers in scope."""
        return await self.eval(script(body, args), timeout)


def page_targets(port):
    host = resolve_host(port)
    if host is None:
        raise CDPError(f"nothing is serving the debugging port on {port}")
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/json/list", timeout=6) as r:
            targets = json.load(r)
    except (urllib.error.URLError, OSError) as exc:
        raise CDPError(
            f"no debugging port on {port} -- start ChatGPT.app with "
            f"--remote-debugging-port={port} ({exc})"
        ) from exc
    return [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]


async def connect_window(port, wait=40):
    """Connect to the app's main window.

    Polls, because the port answers before the UI is usable: on a cold launch
    the window exists and CDP is serving while the app is still signing in and
    painting, and the sidebar only fills in at the end of that. Auxiliary
    windows are skipped by URL -- the hidden avatar overlay carries a composer of
    its own and is listed first, so "the first page with a composer" is the
    wrong window.
    """
    deadline = time.time() + wait
    last = None
    while True:
        for target in page_targets(port):
            url = target.get("url", "")
            if "initialRoute" in url or "detached-window" in url:
                continue
            ws = await websockets.connect(target["webSocketDebuggerUrl"],
                                          max_size=256 * 1024 * 1024)
            page = Page(ws)
            try:
                if await page.run("return !!router() && (!!composer() || projectRows().length > 0);"):
                    return page, ws
            except CDPError as exc:
                last = exc
            await ws.close()
        if time.time() >= deadline:
            raise CDPError(f"no app window with a composer{f' ({last})' if last else ''}")
        await asyncio.sleep(1.0)


def normalize(text):
    return " ".join((text or "").split())


def chat_id(ref):
    """A conversation id out of a bare id or any chatgpt.com chat URL."""
    found = re.findall(UUID, (ref or "").lower())
    if not found:
        raise CDPError(f"not a conversation id or chat URL: {ref!r}")
    return found[-1]


def chat_url(route):
    return WEB_ORIGIN + route if route and "/c/" in route else None


# --- Getting to the right composer ----------------------------------------------

async def open_temporary(page, allow_history):
    """A blank, temporary composer on the plain new chat. Refuses otherwise."""
    res = await page.run("""
      const btn = [...document.querySelectorAll('button')].find(b => shown(b) && !b.getAttribute('aria-label') &&
        ARGS.newChat.includes((b.innerText || '').trim().toLowerCase()));
      if (btn) btn.click(); else router().navigate('/');
      let s = null;
      for (let i = 0; i < 40; i++) {
        await sleep(150);
        s = composerState();
        if (s && !s.projectId && s.route === '/') break;
      }
      if (!s || s.projectId || s.route !== '/') return {error: 'the new-chat view never appeared', state: s};
      if (s.temporary) return {temporary: 'already', state: s};
      const toggle = [...document.querySelectorAll('button')].find(b => shown(b) &&
        new RegExp(ARGS.tempPattern, 'i').test(labelOf(b)));
      if (!toggle) return {temporary: 'absent', state: s};
      toggle.click();
      for (let i = 0; i < 20 && !s.temporary; i++) { await sleep(150); s = composerState() || s; }
      return {temporary: s.temporary ? 'enabled' : 'absent', state: s};
    """, {"newChat": sorted(NEW_CHAT_LABELS), "tempPattern": TEMP_CHAT_PATTERN}, timeout=20)
    if res.get("error"):
        raise CDPError(res["error"])
    # Refuse rather than quietly filing the question in the user's real history:
    # the whole point of the default is that these threads are disposable.
    if res["temporary"] == "absent" and not allow_history:
        raise CDPError("could not switch the new chat to temporary mode — refusing to ask in a "
                       "thread that would be saved to history (pass --allow-history to accept "
                       "that, or --project/--chat to save it on purpose)")
    return res["state"]


async def list_projects(page):
    return await page.run("return projectRows();")


def match_project(rows, query):
    """The one sidebar project `query` names, by id, URL, exact or partial name."""
    q = (query or "").strip()
    pid = re.search(PROJECT_ID, q.lower())
    tiers = [
        lambda r: pid is not None and r["id"] == pid.group(0),
        lambda r: r["name"] == q,
        lambda r: r["name"].casefold() == q.casefold(),
        lambda r: q.casefold() in r["name"].casefold(),
    ]
    for tier in tiers:
        hits = [r for r in rows if tier(r)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise CDPError(f"{q!r} matches several projects: "
                           + ", ".join(repr(r["name"]) for r in hits))
    raise CDPError(f"no project matches {q!r}; the sidebar has: "
                   + ", ".join(repr(r["name"]) for r in rows))


async def open_project(page, query):
    """A blank composer for a new chat inside the named project."""
    project = match_project(await list_projects(page), query)
    state = await page.run("""
      router().navigate('/g/' + ARGS.id + '/project');
      let s = null;
      for (let i = 0; i < 60; i++) {
        await sleep(150);
        s = composerState();
        if (s && s.projectId === ARGS.id && /\\/project$/.test(s.route || '')) return s;
      }
      return {error: 'the project view never appeared', state: s};
    """, {"id": project["id"]}, timeout=20)
    if state.get("error"):
        raise CDPError(f"{state['error']} for project {project['name']!r}")
    return state, project


async def open_chat(page, ref, wait=25):
    """Make the given conversation the one on screen, fully loaded."""
    cid = chat_id(ref)
    res = await page.run("""
      router().navigate('/c/' + ARGS.id);
      for (let i = 0; i < ARGS.polls; i++) {
        await sleep(200);
        const s = composerState();
        const e = turnEntries();
        if (s && s.conversationId === ARGS.id && e.length && e.every(x => x.conversationId === ARGS.id)) return s;
      }
      return {error: 'not found, or it did not finish loading', state: composerState()};
    """, {"id": cid, "polls": int(wait * 5)}, timeout=wait + 10)
    if res.get("error"):
        raise CDPError(f"conversation {cid}: {res['error']}")
    return res


# --- Asking -----------------------------------------------------------------------

async def type_prompt(page, prompt):
    """Put `prompt` into the visible composer and send it."""
    leftover = await page.run("""
      const ed = editor();
      if (!ed) return {error: 'no composer on screen'};
      const draft = (ed.textContent || '').trim();
      ed.focus();
      if (draft) { document.execCommand('selectAll'); document.execCommand('delete'); await sleep(200); }
      return {draft, left: (ed.textContent || '').trim()};
    """)
    if leftover.get("error"):
        raise CDPError(leftover["error"])
    if leftover["left"]:
        raise CDPError("the composer holds unsent text that could not be cleared")
    if leftover["draft"]:
        print(f"chatgpt-ask: discarded an unsent draft: {leftover['draft'][:80]!r}", file=sys.stderr)

    # Injecting at the input layer is what makes ProseMirror update its own
    # state; assigning innerText leaves the editor model empty and the send
    # button never appears.
    await page.call("Input.insertText", {"text": prompt})
    await asyncio.sleep(0.6)
    sent = await page.run("""
      const b = composerButtons().find(b => ARGS.labels.includes(labelOf(b).toLowerCase()));
      if (!b) return false;
      b.click();
      return true;
    """, {"labels": sorted(SEND_LABELS)})
    if not sent:
        raise CDPError("send button never appeared -- the composer stayed empty")


LAST_TURN = """
  const e = turnEntries();
  const s = composerState();
  const streaming = composerButtons().some(b => ARGS.stop.some(l => labelOf(b).toLowerCase().includes(l)));
  return {count: e.length, turn: e.length ? renderTurn(e[e.length - 1]) : null, streaming, state: s};
"""


async def ask(page, prompt, timeout, settle=1.5, poll=0.8):
    started = time.time()
    stop = {"stop": sorted(s.lower() for s in STOP_LABELS)}
    baseline = (await page.run(LAST_TURN, stop, timeout=60))["count"]
    await type_prompt(page, prompt)

    # Ours is the last turn once it carries the question's opening words, or once
    # the thread has grown past where it was -- the composer may rewrite leading
    # markdown, so the words alone are not a reliable signal.
    anchor = normalize(prompt)[:80]

    def ours(snap):
        turn = snap.get("turn") or {}
        return bool(turn) and (anchor in normalize(turn.get("user")) or snap["count"] > baseline)

    # Done means: our turn is the last one, the app no longer marks it pending,
    # the stop control is gone, and the answer has not changed for `settle`
    # seconds. Generation pauses mid-answer, so text stability alone would cut
    # long replies short.
    last, stable_since, snap = None, None, {}
    deadline = started + timeout
    while time.time() < deadline:
        await asyncio.sleep(poll)
        snap = await page.run(LAST_TURN, stop, timeout=60)
        if not ours(snap):
            continue
        turn = snap["turn"]
        answer = turn.get("answer", "")
        if snap["streaming"] or turn.get("pending") or not answer:
            last, stable_since = answer, None
            continue
        if answer != last or stable_since is None:
            last, stable_since = answer, time.time()
            continue
        if time.time() - stable_since >= settle:
            return result(True, turn, snap["state"], started)
    turn = snap["turn"] if snap and ours(snap) else {}
    return result(False, turn, snap.get("state"), started, f"timed out after {timeout}s")


def result(ok, turn, state, started, error=""):
    state = state or {}
    # A chat started from a blank composer keeps its local draft id in the
    # composer; the server id only shows up in the route.
    in_route = re.findall(UUID, state.get("route") or "")
    return {
        "ok": ok, "answer": (turn.get("answer") or "").strip(),
        "elapsed_s": round(time.time() - started, 1), "error": error,
        "chat_url": None if state.get("temporary") else chat_url(state.get("route")),
        "conversation_id": in_route[-1] if in_route else state.get("conversationId"),
        "project_id": state.get("projectId"),
        "temporary": state.get("temporary"),
        "skills": turn.get("skills", []),
    }


# --- Reading ---------------------------------------------------------------------

async def read_chat(page, ref):
    state = await open_chat(page, ref)
    data = await page.run("""
      const s = composerState();
      const p = s.projectId ? projectRows().find(r => r.id === s.projectId) : null;
      return {state: s, title: titleOf(s.conversationId), project: p || (s.projectId ? {id: s.projectId, name: null} : null),
              turns: turnEntries().map(renderTurn)};
    """, timeout=120)
    data["chat_url"] = chat_url(data["state"]["route"])
    return data


async def project_detail(page, query):
    project = match_project(await list_projects(page), query)
    data = await page.run("""
      const row = document.querySelector('[data-app-action-sidebar-project-id="' + ARGS.id + '"]');
      const collapsed = row.getAttribute('aria-expanded') === 'false';
      if (collapsed) row.click();
      const box = () => document.querySelector('[data-sidebar-project-container-id="project:' + ARGS.id + '"]');
      const rows = () => box() ? [...box().querySelectorAll('[data-sidebar-cycle-item][role=button]')] : [];
      for (let i = 0; i < 20 && collapsed && !rows().length; i++) await sleep(150);
      const chats = rows().map(el => {
        const p = propsUp(el, p => p.conversation && p.conversation.id, 15);
        return p && {id: p.conversation.id, title: p.conversation.title, updated: p.conversation.update_time};
      }).filter(Boolean);
      const gp = box() && propsUp(box(), p => p.project && p.project.project && p.project.project.gizmo, 10);
      const g = gp ? gp.project.project : null;
      if (collapsed) row.click();
      return {
        chats,
        short_url: g && g.gizmo.short_url,
        instructions: g ? g.gizmo.instructions || '' : null,
        files: g ? (g.files || []).map(f => f.name || f.file_name || f.id) : [],
        memory: g && g.gizmo.memory_scope,
        sharing: g && g.gizmo.share_recipient,
      };
    """, {"id": project["id"]}, timeout=30)
    data.update(project)
    data["url"] = f"{WEB_ORIGIN}/g/{data.get('short_url') or project['id']}/project"
    for chat in data["chats"]:
        chat["url"] = f"{WEB_ORIGIN}/g/{project['id']}/c/{chat['id']}"
    return data


def render_transcript(data):
    title = data.get("title") or data["state"]["conversationId"]
    out = [f"# {title}", "", f"- chat: {data['chat_url']}"]
    if data.get("project"):
        p = data["project"]
        out.append(f"- project: {p.get('name') or '?'} ({p['id']})")
    out += [f"- turns: {len(data['turns'])}", ""]
    for n, turn in enumerate(data["turns"], 1):
        out += ["---", "", f"## {n} · User", "", turn["user"].strip(), ""]
        head = f"## {n} · ChatGPT"
        if turn["skills"]:
            head += f" (skills: {', '.join(turn['skills'])})"
        out += [head, "", turn["answer"] or "_(no text answer)_", ""]
        if turn["files"]:
            out.append("Files: " + ", ".join(
                f["name"] + (" (generated by ChatGPT, not downloaded)" if f["generated"] else "")
                for f in turn["files"]))
            out.append("")
    return "\n".join(out)


def render_project(data):
    out = [f"# {data['name']}", "", f"- id: {data['id']}", f"- url: {data['url']}"]
    if data.get("memory"):
        out.append(f"- memory: {data['memory']}")
    if data.get("sharing"):
        out.append(f"- sharing: {data['sharing']}")
    out += ["", "## Chats (as listed in the sidebar)", ""]
    out += [f"- {c['title']} — {c['url']} (updated {c.get('updated') or '?'})" for c in data["chats"]] \
        or ["_(none)_"]
    if data.get("files"):
        out += ["", "## Files", ""] + [f"- {f}" for f in data["files"]]
    out += ["", "## Instructions", "", data.get("instructions") or "_(none)_"]
    return "\n".join(out)


# --- Entry point -------------------------------------------------------------------

def doctor(port):
    """Report what this host allows, and whether a call would get through.

    Worth having because the interesting failures happen before any of this
    script's own logic runs -- a sandbox with no networking, a port nobody is
    serving -- and each host reports them differently, or not at all.
    """
    label, may_manage, why_not = host()
    blocked = unreachable_reason()
    serving = resolve_host(port)
    installed = os.path.exists(APP_BINARY)

    def row(label, value):
        print(f"{label:<15}: {value}")

    row("host", label)
    row("platform", sys.platform)
    row("ChatGPT.app",
        f"{APP_PATH} ({'running' if app_is_running() else 'not running'})"
        if installed else "not installed")
    row(f"port {port}", f"open on {serving}" if serving else "closed")
    row("app management", "allowed" if may_manage else f"blocked, {why_not}")

    if blocked:
        row("verdict", f"unusable -- {blocked}")
        return 1
    if serving or may_manage:
        row("verdict", "ready")
        return 0
    row("verdict", f"blocked -- {OPEN_IT_YOURSELF}")
    return 1


async def run(args, prompt):
    """Do what the arguments ask; return (result object, text for stdout)."""
    page, ws = await connect_window(args.port)
    started = time.time()
    try:
        if args.read:
            data = await read_chat(page, args.read)
            return {"ok": True, **data}, render_transcript(data)
        if args.list_project is not None:
            if not args.list_project:
                rows = await list_projects(page)
                return {"ok": True, "projects": rows}, "\n".join(
                    f"{r['name']} — {r['id']}" for r in rows)
            data = await project_detail(page, args.list_project)
            return {"ok": True, **data}, render_project(data)

        if args.project:
            await open_project(page, args.project)
        elif args.chat:
            await open_chat(page, args.chat)
        elif not args.keep_chat:
            await open_temporary(page, args.allow_history)

        state = await page.run("return composerState();")
        if not state:
            raise CDPError("no composer on screen")
        if not state["temporary"] and args.tag:
            prompt = f"{args.tag} {prompt}"
        res = await ask(page, prompt, args.timeout)
        res["elapsed_s"] = round(time.time() - started, 1)
        return res, res["answer"]
    finally:
        await ws.close()


def main():
    ap = argparse.ArgumentParser(description="Ask, read, or list ChatGPT.app conversations over CDP.")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--prompt")
    src.add_argument("--prompt-file")
    where = ap.add_mutually_exclusive_group()
    where.add_argument("--project", metavar="NAME|ID|URL",
                       help="ask in a new chat inside this project (saved to it)")
    where.add_argument("--chat", metavar="ID|URL",
                       help="ask in this existing conversation (saved to it)")
    where.add_argument("--keep-chat", action="store_true",
                       help="ask in whatever thread is open, instead of a new temporary one")
    where.add_argument("--read", metavar="ID|URL",
                       help="print this conversation's transcript as markdown; sends nothing")
    where.add_argument("--list-project", nargs="?", const="", metavar="NAME|ID|URL",
                       help="list the sidebar's projects, or one project's chats, files and "
                            "instructions; sends nothing")
    ap.add_argument("--timeout", type=int, default=None,
                    help="seconds to wait for the answer (default 300; 900 with "
                         "--project/--chat/--keep-chat)")
    ap.add_argument("--port", type=int, default=9222, help="CDP port ChatGPT.app listens on")
    ap.add_argument("--json", action="store_true", help="emit the full result object")
    ap.add_argument("--tag", default=DEFAULT_TAG,
                    help=f"prefix for questions that land in a saved thread (default "
                         f"{DEFAULT_TAG!r}; '' for none)")
    ap.add_argument("--allow-history", action="store_true",
                    help="proceed even if temporary chat is unavailable, saving the thread "
                         "to the account's history")
    ap.add_argument("--keep-app", action="store_true",
                    help="leave the app running afterwards even if this call started it "
                         "(for a run of several calls — saves a relaunch each time)")
    ap.add_argument("--doctor", action="store_true",
                    help="report what this host allows and exit, without touching the app")
    args = ap.parse_args()

    if args.doctor:
        sys.exit(doctor(args.port))

    asking = not (args.read or args.list_project is not None)
    prompt = ""
    if asking:
        if not (args.prompt or args.prompt_file):
            ap.error("one of --prompt or --prompt-file is required")
        prompt = args.prompt
        if args.prompt_file:
            with open(args.prompt_file, encoding="utf-8") as fh:
                prompt = fh.read()
        prompt = (prompt or "").strip()
        if not prompt:
            sys.exit("empty prompt")
    elif args.prompt or args.prompt_file:
        ap.error("--read and --list-project send nothing; drop --prompt/--prompt-file")
    if args.timeout is None:
        args.timeout = 900 if (args.project or args.chat or args.keep_chat) else 300

    we_started = False
    text = ""
    try:
        blocked = unreachable_reason()
        if blocked:
            raise CDPError(blocked)
        we_started = ensure_app(args.port)
        result, text = asyncio.run(run(args, prompt))
    except CDPError as exc:
        result = {"ok": False, "answer": "", "elapsed_s": 0, "error": str(exc)}
    finally:
        # Only clean up an app this call launched: one that was already serving
        # the port belongs to the user's session and is not ours to close.
        if we_started and not args.keep_app:
            quit_app()

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    elif result["ok"]:
        print(text)
        if result.get("chat_url") and asking:
            print(f"chat: {result['chat_url']}", file=sys.stderr)
    else:
        print(result["error"], file=sys.stderr)
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
