# Bohrium CLI Reference


## Job Submit (for single-job submission)

```bash
bohr job submit \
  --job_name "Name" \
  --project_id <id> \
  --machine_type "<type>" \
  --image_address "<registry>" \
  --command "<cmd>" \
  --log_file "<file>" \
  --backward_files "<f1>,<f2>" \
  --max_run_time <minutes> \
  --input_directory "./"
```

Options:
- `--job_name`: name of the job
- `--project_id`: project ID
- `--image_address`: image address on bohrium.
- `--machine_type`: `c2_m4_cpu`, `c4_m15_1`, `c32_m64_cpu`, or with GPU: `1 * NVIDIA L20_48g`, `1 * NVIDIA V100_32g`
- `--max_run_time`: in minutes. Default is unlimited.
- `--command`: command to run in the container.
- `--backward_files`: comma-separated string (NOT array). Should include all critical result files,
   log files and directories. Supports "*" wildcard. Example: `"output,log,*.csv"`.
- `--input_directory`: local dir to upload as job input. Usually uses the current directory `./`,
   which means all files in the current directory will be uploaded, and you have to make sure to `cd`
   into the right directory before running `bohr job submit` from there.
- `--nnode`: number of compute nodes to use for the job (default 1, more than 1 not tested and therefore not recommended)
- `--max_reschedule_times`: auto-retry count if submission fails (default 0, but recommend 3 for robustness against network issues)

> **Note**: `bohr job submit` prints out the job ID and the job group ID when finished.
> Keep these IDs along with the job name for quick reference in job management.

An example to submit a lammps job:
```bash
bohr job submit \
  --project_id "YOUR_PROJECT_ID" \
  --job_name "job-name" \
  --machine_type "c8_m32_cpu" \
  --image_address "registry.dp.tech/dptech/lammps:2023.08.02" \
  --input_directory "./" \
  --backward_files "output.txt,results.log" \
  --command "your command here"
```

A typical output of `bohr job submit` is:
```text
Submit job succeed.
JobId:  23049975
JobGroupId:  16375478
```
To store the job ID and job group ID for later use, you can wrap the submit command to capture
these IDs in the output or dump the output to a text file for future reference.

## Job Management
The following commands are used to query jobs, download results, check logs, and terminate jobs.
All of them require you to specify the job ID or job group ID, which can be read from the captured
output of `bohr job submit`.

```bash
bohr job list -j <jobGroupId>              # Interactively List jobs in group (active only by default)
bohr job list -j <jobGroupId> --json       # JSON output with all active jobs only. 
bohr job list -j <jobGroupId> -i           # Finished only
bohr job list -j <jobGroupId> -f           # Failed only
bohr job list -j <jobGroupId> -r           # Running only
bohr job list -j <jobGroupId> -p           # Pending only
bohr job list -j <jobGroupId> -s           # Scheduling only
bohr job list -j <jobGroupId> -d           # Stopped only
bohr job log -j <jobId> -o ./dir/          # Download logs from a single job (optional at job finish, used for status checking)
bohr job download -j <jobId> -o ./dir/     # Download results from a single job.
bohr job kill -j <jobId>                   # Kill job
```
> **Note**: Default listing shows active jobs only — completed jobs are **not** shown unless `-i` is used.

Check for job status periodically (for example, every 10 minutes) to see if the job has finished.
- If not finished, check whether the job is running or pending, and whether it has stayed in the same state
   for too long (for example, over 5 hours).
- If pending, the specified machine type may be busy. Try another appropriate machine type.
- If running, check the logs to see if the job is stuck (no change of log at all).
- If the job is stuck for too long and there is no excuse for this happening, judging by your skills and memory,
   kill the job, report the problem, and try to analyze the issue given the logs and result files.

An example to periodically check job status (but not checking the logs, which you may want to do manually):
```bash
GROUP_ID=$(cat .bohrium_job_group_id)
while true; do
  OUTPUT=$(timeout 90 bohr job list -j "$GROUP_ID" --json 2>/dev/null)
  TOTAL=$(echo "$OUTPUT" | jq 'length')
  DONE=$(echo "$OUTPUT" | jq '[.[] | select(.status == "Finished" or .status == "Failed" or .status == "Cancelled" or .status == "Terminated")] | length')
  FAILED=$(echo "$OUTPUT" | jq '[.[] | select(.status == "Failed" or .status == "Cancelled" or .status == "Terminated")] | length')
  echo "[$(date '+%H:%M:%S')] $DONE/$TOTAL jobs done, $FAILED failed"
  if [ "$DONE" -eq "$TOTAL" ] && [ "$TOTAL" -gt 0 ]; then
    [ "$FAILED" -gt 0 ] && echo "$FAILED job(s) failed!" && break
    echo "All $TOTAL jobs finished successfully!" && break
  fi
  sleep 60
done
```


## Job Group (for batch submission)

```bash
bohr job_group create -n <name> -p <project_id>    # Create group, returns job_group_id
bohr job_group list --json                          # List groups
bohr job_group download -j <groupId> -o ./dir/     # Download all results for group
bohr job_group terminate <groupId>                  # Terminate all jobs in group
bohr job_group delete <groupId>                     # Delete group
```

For multiple related jobs, create a job group first, then submit all jobs under it.
This enables easy bulk result download and centralized management. For example:

1. Create a job group:
```bash
bohr job_group create -n "my-batch" -p "$PROJECT_ID"
```
This will print a job group ID, which you should keep a record of, and can submit jobs and download results under it.
One example to capture JOB_GROUP_ID is:
```bash
JOB_GROUP_ID=$(bohr job_group create -n "my-batch" -p "$PROJECT_ID" | grep -oP '\d+')
echo "$JOB_GROUP_ID" > .bohrium_job_group_id
```

2. Submit multiple jobs under the group

```bash
for CALC_DIR in <calc_dir_1> <calc_dir_2> ...; do
  bohr job submit \
    --project_id "$PROJECT_ID" \
    --job_name "$(basename $CALC_DIR)"   \
    --machine_type "c32_m128_cpu" \
    --image_address "$IMAGE" \
    --input_directory "$CALC_DIR/" \
    --job_group_id <group_id> \
    --command "your command here"
done
```

3. Download all results at once
```bash
bohr job_group download -j <group_id> -o ./output/
```

## Project & Node & image finding
You can get currently available project IDs, nodes and images using the following commands:
```bash
bohr project list --json                   # List projects (non-interactive)
bohr node list                             # List compute nodes
bohr image list                            # List available images
```

However, the result of these commands are **only for checking** possible misspecifications by the user.
Use the following preference for actual choice of project, node and image:

- Project ID: User specified `BOHRIUM_PROJECT_ID` > The first project ID in `bohr project list --json`
- Node & Image: User required (ususally none) > hard-coded option in your skill documents, if any > The
         most appropriate option inferred from your skills, memories and the job type > The 
         first available node in `bohr node list`
- Actually, when having to use the last option, the user should always be warned to specify the node and image.
   Never do that silently!
