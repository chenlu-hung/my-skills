# DiCOSApp: GPU sessions through the browser

DiCOSApp (<https://dicos.grid.sinica.edu.tw/dockerapps/>) launches container apps, such as
JupyterLab, Code Server or MATLAB, on GPU cards that Slurm does not offer: RTX 4090, L40S and
RTX 3090. It is driven from a browser, not from `ssh dicos`. Observed 2026-11-14 unless dated
otherwise.

## Sign-in

The portal uses the user's DiCOS web login. The user signs in themselves in the browser the
agent uses (in Claude Code desktop: the built-in browser pane). Never type the password or OTP.
If the page shows a login form, stop and ask the user to sign in there.

## Cards and launching

- Each card names an app and a GPU, for example `Jupyter Lab GPU rtx4090`. "Resources: N%" is
  a pool indicator, not a guarantee of a free GPU; it dropped from 43% to 37% when one RTX 4090
  session started.
- `Launch` opens a menu of durations: 2 hours, 3 days, 7 days, 10 days. Each item is
  `a.launchbtn` with `data-app` (card id) and `data-date` (hours). The session ends by itself
  at the End Time shown under "Currently Open Apps".
- **One session per card.** After launch the card's button becomes `Open`, so parallel runs
  on the same GPU model need different cards for that model (for RTX 4090:
  `jupyterlabgpurtx4090`, `jupyterlabtf24gpu4090`, Code Server RTX4090, MATLAB rtx4090 and
  others). Check that a card mounts `/ceph/work` before relying on it.
- **Each session has one GPU**, and on the RTX 4090 JupyterLab about 4.5 CPUs
  (`cat /sys/fs/cgroup/cpu.max` = `450000 100000`, although `nproc` reports 40). Size
  DataLoader workers to the cgroup, not to `nproc`.
- Launching takes about 20 seconds. Read the `Open` link's `href` rather than clicking it:
  `http://scale-k8s-master01.twgrid.org:<port>/?token=<token>`. The token is a credential.
  Do not print it in chat, logs, commits or reports.

| Card (JupyterLab) | Mounts `/ceph/work` | `git` | GPU seen |
|---|---|---|---|
| GPU rtx4090 | yes | yes | GeForce RTX 4090 24 GB, driver 595.58.03 |
| GPU L40s | yes | yes | L40S |
| GPU A100 CUDA 13 | yes | yes | A100 80 GB |
| GPU A100 | no | no | |
| GPU with Tensorflow 3090 | no | | |
| GPU rtx4090, version "GPU with Tensorflow v2.4" | no (home only) | | GeForce RTX 4090 |
| GPU L40s CUDA 13 | yes | yes | L40S 46 GB, 7.5 CPUs, no `ps` in the image |

**The "CUDA 13" L40S card ships an old forward-compat `libcuda` (580.x in
`/usr/local/cuda-13.0/compat`) that `ldconfig` prefers over the host driver (595.58), so every
PyTorch build fails with `Error 803: system has unsupported display driver / cuda driver
combination` and `torch.cuda.is_available()` is False.** Prefix the run with
`LD_LIBRARY_PATH=/lib64`, which loads the host's `libcuda` and works for both cu126 and cu130
builds. Check `torch.cuda.is_available()` on any new card before starting a long run.

The L40S cards have 7.5 CPUs (`cpu.max` `750000 100000`) against the RTX 4090's 4.5.
Code Server cards (RTX4090, L40s) open a "Launch VSCode" dialog that requires setting a login
password first, so the user has to launch those; an agent does not type passwords. They also
offer different durations (2 hours, 1, 3 or 5 days).

## Billing

DiCOSApp is paid per GPU-day like Slurm partitions. It bills the time the session exists, from
launch until it is deleted or reaches its End Time, not the duration chosen at launch
(confirmed by the user on 2026-11-19). A long reservation costs nothing extra, but an idle
session still bills. Treat a launch as a paid job: get budget approval first, estimate cost from
the expected running time, queue work so the GPU is not idle, and press **Delete** as soon as
the work ends. Deleting kills every process in the session, and there is no confirmation
dialog: the button turns into "Deleting...".

- Each running session shows `Open` and `Delete` both on its card and under "Currently Open
  Apps" (`a.terminatebtn` with the card's `data-app`). The "Currently Open Apps" panel is
  collapsed until its title is clicked, and right after a launch a session may appear only on
  its card. Locate the card by its version text, because several cards share a title (two
  "Jupyter Lab GPU rtx4090" cards exist).
- Before launching on a new card, check right away that it mounts what the work needs
  (`ls /ceph/work/<group>`), and delete it at once if not.

## Running commands without clicking in the terminal

JupyterLab's REST API is reachable from the page itself, so a browser JavaScript tool can open
a terminal and send commands. Run this in the JupyterLab tab:

```js
const xsrf = (document.cookie.match(/_xsrf=([^;]+)/) || [])[1] || '';
const {name} = await (await fetch('/api/terminals',
  {method: 'POST', headers: {'X-XSRFToken': xsrf}})).json();
window.run = (cmd, ms = 8000) => new Promise(done => {
  const ws = new WebSocket(location.origin.replace('http', 'ws') + '/terminals/websocket/' + name);
  let out = '';
  ws.onmessage = e => { const m = JSON.parse(e.data); if (m[0] === 'stdout') out += m[1]; };
  ws.onopen = () => ws.send(JSON.stringify(['stdin', cmd + '\n']));
  setTimeout(() => { ws.close(); done(out.replace(/\x1b\[[0-9;?]*[a-zA-Z]/g, '')); }, ms);
});
await run('nvidia-smi --query-gpu=name,memory.total --format=csv; cat /sys/fs/cgroup/cpu.max');
```

The terminal persists between calls, and `window.run` survives until the tab reloads. Run it
only after JupyterLab has finished loading; on a just-opened tab the first call can hang until
the tool times out. A call that times out may already have created a terminal, so check the
log or state before sending the command again. Each session has its own origin
(`:<port>`), so keep one browser tab per session.

## Long runs

- Start them detached so that closing the tab does not stop them, and log to Ceph so progress
  can be read with `ssh dicos` from outside the session:

  ```sh
  L=/ceph/work/<group>/<Project>/logs/run-$(date -u +%Y%m%dT%H%M%SZ).out
  setsid nohup ./run.sh > "$L" 2>&1 < /dev/null & echo "$L"
  ```

- Put several runs in one script to run back to back, so the GPU is not idle between them.
- Monitor from the login node with `ssh dicos 'tail -n 20 <log>'`. The login node cannot see
  the session's GPU or processes; a run should write its own progress and an end marker.
- Use the shared conda envs in the group space by full path (`$ROOT/envs/<env>/bin/python`).

## Getting code into a session

- Compute containers have no credentials for private GitHub repositories. Sync from the
  workstation over `ssh dicos` into the group space, and run from there.
- An agent's git worktree has a `.git` *file*, not a directory. Mirroring a worktree with
  `rsync --delete` over a full checkout replaces the remote `.git` directory and breaks it.
  Send the commits instead:

  ```sh
  git bundle create /tmp/x.bundle <commit already on DiCOS>..<branch>
  scp /tmp/x.bundle dicos:/ceph/work/<group>/<Project>/scratch/
  ssh dicos 'cd <checkout> && git fetch ../scratch/x.bundle <branch>:<branch> && git checkout <branch>'
  ```

- Do not switch the checkout that running jobs use; Python may import modules lazily.
- Without `ssh dicos`: small files can be written with the Jupyter contents API
  (`PUT /api/contents/<path>`, `{"type":"file","format":"base64","content":...}`, relative to
  the Jupyter root, which is the home directory). The browser blocks the http JupyterLab page
  from fetching `http://127.0.0.1` (Private Network Access), so a local file server does not
  help.
