---
name: dicos
description: Run work on the DiCOS / NSTCCore (ASGC, twgrid) Slurm cluster through the `ssh dicos` alias — check the 2FA-authenticated connection, choose between home and group storage, sync code and data, pick a GPU or CPU partition, write and submit sbatch jobs, build conda environments, and monitor or debug jobs. Runs the same way in Claude Code, Codex, opencode, and pi. Use when the user mentions DiCOS, NSTCCore, twgrid, ASGC, `ssh dicos`, slurm-ui, /ceph/work, or says "丟到 DiCOS", "上 DiCOS 跑", "DiCOS 訓練", "送 slurm", "交 job", "群組空間", "submit a job on DiCOS", "train on the cluster".
---

# DiCOS

Slurm cluster run by ASGC. Manual: <https://nstccore.twgrid.org/documents/>. The
limits below were read from the cluster on 2026-11-08. When a decision depends on
one, re-check it with the command given.

## Connect first

Login needs a password and an OTP, which only the user can enter. An ssh alias keeps
one authenticated master connection, and every later `ssh` or `rsync` reuses it
without prompting. If `~/.ssh/config` has no `Host dicos`, ask the user for their
DiCOS account and suggest this entry:

```
Host dicos
    HostName slurm-ui.twgrid.org
    User <DiCOS account>
    ControlMaster auto
    ControlPath ~/.ssh/cm-%C
    ControlPersist 12h
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

Then check the connection:

```sh
ssh -O check dicos          # "Master running" means ready
```

If it is not running, stop and ask the user to run this in their own terminal, then
re-check:

```sh
ssh -fN dicos
```

Never ask for the password or OTP in chat, and never pipe them into ssh. Always pass
`-o BatchMode=yes` on remote commands, so a dropped connection fails instead of
hanging on a prompt:

```sh
ssh -o BatchMode=yes dicos 'squeue -u $USER'
ssh -o BatchMode=yes dicos 'bash -lc "module avail 2>&1 | grep -i cuda"'
```

If the check fails from inside an agent sandbox (Codex's, for one) while the user's
terminal is connected, the sandbox is blocking the control socket or the network.
Request permission to run outside the sandbox.

## Where things go

Find the account's group first, and substitute it for `<group>` below:

```sh
ssh -o BatchMode=yes dicos 'id -gn; ls -l "/ceph/work/$(id -gn)"'
```

| Space | Path | Size | Use |
|---|---|---|---|
| Home | `/dicos_ui_home/<DiCOS account>` | 100 GB | Shell config and small files. No long-term guarantee. |
| Group | `/ceph/work/<group>` | 3 TiB by default, shared | Projects, conda envs, and all large or reused data. |
| Tape | `/ceph/project/<group>` | By application | Paid long-term backup, only on request. See `references/recipes.md`. |

- **Large training data that gets reused goes in the group space**, as a top-level
  dataset directory, `/ceph/work/<group>/<dataset>/`, so that projects and group
  members share one copy.
- A project lives at `/ceph/work/<group>/<Project>/` with `code/`, `envs/`, `logs/`,
  and `runs/`.
- **Exceeding the home quota locks the account out of login.** Keep caches out of
  home by exporting these in jobs that install or download:
  `CONDA_PKGS_DIRS`, `PIP_CACHE_DIR`, `HF_HOME`, `TORCH_HOME`.
- Everything in the group space is shared. Before deleting or overwriting, check the
  owner with `ls -l`, and do not touch another member's directories without asking.
- Ceph degrades with many files in one directory. Prefer a few large archives or
  shards (Zarr, HDF5, WebDataset) to millions of small files.

```sh
G=/ceph/work/$(id -gn)
getfattr --only-values -n ceph.quota.max_bytes $G; echo   # quota in bytes
getfattr --only-values -n ceph.dir.rbytes $G; echo        # used bytes, instant
getfattr --only-values -n ceph.dir.rfiles $G; echo        # file count
du -sh ~                                                  # home has no Ceph attrs
```

## Moving data

```sh
rsync -avh --partial --exclude '.DS_Store' ./data/ dicos:/ceph/work/<group>/<dataset>/
```

Add `--delete` only for a directory that is purely a mirror, and only after a
`--dry-run`. Check the free quota before uploading a large dataset.

## Rules

- Login nodes are only for editing, submitting, monitoring, and transfers. Admins
  kill heavy processes without notice. **Compiling, `conda create`, `pip install`,
  training, and data processing all go in a job.**
- Compute nodes cannot be reached by ssh. An agent should use `sbatch`, because
  `salloc` needs a held-open terminal.
- Confirm before submitting long or multi-GPU jobs, and before cancelling a job the
  current task did not start.

## Partitions

Time limits are hard limits. Check live state with
`sinfo -p a100-al9,b6000-al9,v100-al9 -N -O NodeHost:15,Gres:10,GresUsed:12,StateCompact`.

| GPU | Partition (limit) | Nodes | Per node |
|---|---|---|---|
| A100 | `a100_devel-al9` (20 min, 1 GPU), `a100_short-al9` (6 h), `a100-al9` (5 d), `a100_long-al9` (7 d) | 2 | 8 GPU, 64 CPU, 1 TB |
| V100 32 GB | `v100-al9_short` (6 h), `v100-al9` (5 d), `v100-al9_long` (7 d) | 6 | 8 GPU, 32+ CPU, 770 GB |
| B6000 | `b6000-al9_short` (6 h), `b6000-al9` (5 d), `b6000-al9_long` (7 d), `b6000-al9_devel` (2 GPU) | 1 | 8 GPU, 64 CPU, 1.5 TB |

The A100 and B6000 model details come from partition and node names only. Log
`nvidia-smi` in the job when the exact card matters. V100 nodes run driver 575.51.03.

CPU partitions come in three families: `edr1-al9` (20 nodes, 192 CPU, 1.4 TB),
`edr2-al9` (10 nodes, 192 CPU, 1.5 TB), and `intel-g4-al9` (4 nodes, 128 CPU, 927 GB).
Each has `_short` (3 h), `_short_serial` (4 h), `_moderate_serial` (2 d),
`_long_serial` (14 d), and `_large` (14 d). The default is `edr1-al9_short`, and
`edr1-al9_short_serial` caps a job at 24 CPUs. `bigmemory_hdr1` runs up to 14 days.
Leave `reservation_*` and course partitions alone.

GPU limits come from QoS: a job needs at least 1 GPU and may use at most 8. A GPU
partition rejects a job without `--gres=gpu:N` (error `QOSMinGRES`). No `--account` or
`--qos` is needed. There is no way to select a GPU model within a partition, so the
partition picks the model.

Queues differ a lot between GPU types. Compare expected start times without submitting
anything:

```sh
for p in a100_short-al9 v100-al9_short b6000-al9_short; do
  sbatch --test-only -p $p --gres=gpu:1 -c 4 -t 60 --wrap=hostname
done
```

## Job template

```bash
#!/bin/bash -l
#SBATCH --job-name=<name>
#SBATCH --partition=v100-al9_short
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --gres=gpu:1
#SBATCH --time=02:00:00
#SBATCH --output=/ceph/work/<group>/<Project>/logs/%x-%j.out
set -euo pipefail
ROOT=/ceph/work/<group>/<Project>
cd "$ROOT/code"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
"$ROOT/envs/<env>/bin/python" train.py
```

- Create the `logs/` directory first. Slurm does not create it, and the job dies
  within seconds with no log (typically `FAILED 0:53`).
- Always set `--time`. Without it the job asks for the partition's full limit and
  waits longer.
- Set `--mem`. The default is only 6000 MB per CPU.
- Keep the `-l` in the shebang, so that `module` is defined.

## Conda environments

Install under the project's `envs/` with a prefix, in a CPU job:

```bash
#SBATCH --partition=edr1-al9_short
#SBATCH --cpus-per-task=4
#SBATCH --mem=48G
#SBATCH --time=01:00:00
module load anaconda3
export CONDA_PKGS_DIRS=$ROOT/.cache/conda_pkgs PIP_CACHE_DIR=$ROOT/.cache/pip
conda create -y -p "$ROOT/envs/<env>" --override-channels -c conda-forge python=3.12 pip
"$ROOT/envs/<env>/bin/python" -m pip install --extra-index-url https://download.pytorch.org/whl/cu126 torch
```

- Give an install with PyTorch at least 48 GB. A 16 GB job reached its limit and died
  partway through `pip install torch`, reported only as `FAILED 7:0`.
- Compute nodes have internet access: GitHub, PyPI, conda-forge, and the PyTorch index
  all worked from `edr1`. Call the env's Python by full path, with no `conda activate`.
- Other software comes from `module avail`, including `cuda/12.x–13.1`, `gcc`, `nvhpc`,
  and OpenMPI builds. Containers need `module load singularity` (4.1.2). Docker is not
  available.

## Monitoring and debugging

```sh
squeue -u $USER --start                     # state and expected start
sacct -X -j <id> --format=JobID,JobName%20,Partition,State,ExitCode,Elapsed,MaxRSS,ReqMem
scontrol show job <id>                      # full spec and pending Reason
tail -n 50 <log>
scancel <id>
```

Plain `sacct` covers only the last 24 hours, so add `-S YYYY-MM-DD` for older jobs. When
a job fails, compare `MaxRSS` with `ReqMem` before reading the log: memory deaths do not
always show up as `OUT_OF_MEMORY`.

Job arrays, dependencies, tape restore, account renewal, and support contacts are in
`references/recipes.md`.
