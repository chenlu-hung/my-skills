# DiCOS recipes

Less common procedures, condensed from <https://nstccore.twgrid.org/documents/>.

## Job arrays

```bash
#SBATCH --array=0-4
#SBATCH --output=%A_%a.out
#SBATCH --error=%A_%a.err
LINE=$(sed -n "$((SLURM_ARRAY_TASK_ID+1))p" args.txt)
```

Index forms: `0-10`, `1,3,5,7`, `1-7:2` (step), `1-10%4` (at most 4 at once).

## Dependencies

```bash
JOB1=$(sbatch --parsable preprocess.sh)
JOB2=$(sbatch --parsable --dependency=afterok:$JOB1 train.sh)
```

Types are `after`, `afterany`, `aftercorr`, `afternotok`, and `afterok`. Join with `,`
for AND and `?` for OR.

## Pending reasons

| Reason | Meaning |
|---|---|
| `Priority` | Other jobs are ahead in the queue. |
| `Resources` | Waiting for nodes or GPUs to free up. |
| `Dependency` | Waiting for another job. |
| `ReqNodeNotAvail` | The requested nodes are down, drained, or reserved. |
| `PartitionTimeLimit` | `--time` exceeds the partition limit, so the job will never start. Fix it. |

## Building compiled code

Compile on the same CPU family as the target partition, inside a job. AMD partitions
(`edr1`) use `module load aocc` or `gcc`. Intel partitions (`intel-g4`) use
`module load icc` or `gcc`. `nvhpc` is also available.

## Tape backup (`/ceph/project/<group>`)

This is a paid, project-only service. Apply by emailing the amount to back up, the
folder path, and your requirements to DiCOS-Support@twgrid.org.

- File list: `/ceph/project/common_use/{Project Name}_amadmin.txt`. Search it with `grep`.
- Restore: `recover "$input_disk" YYYY-mm-dd hh:mm:ss`, where `$input_disk` is the
  project directory to restore.
- Queue state: `cat /ceph/project/common_use/online_user_count{n}.txt` for drives 0, 1, 3.
- Copy restored data elsewhere promptly. Running another restore can overwrite the
  default restore directory.

## Expanding group space

The default is 3 TB. The PI applies in TB units at
<https://dicos.grid.sinica.edu.tw/accounting/subscribe-storage/>.

## Accounts

- Passwords and accounts expire every 180 days. Renew by updating the password at
  <https://canew.twgrid.org/ApplyAccount/nocertModify.php>.
- An expired account can be restored for 6 months. After that the owner and PI are
  notified, and it is deleted 1 month later.
- 2FA setup: <https://canew.twgrid.org/ApplyAccount/howToSave2faCode.php>.
- The login address `slurm-ui.twgrid.org` is a round-robin over `slurm-ui01` to
  `slurm-ui03`, and SFTP also works through
  `dicos-sftp.twgrid.org`.

## Support

DiCOS-Support@twgrid.org, 02-2789-8313 (Mon–Fri 09:00–17:00).
