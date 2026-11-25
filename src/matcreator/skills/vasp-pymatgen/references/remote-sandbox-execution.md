# VASP execution on Bohrium sandboxes

Use this route when the user chooses a Bohrium interactive sandbox. Prepare the
calculation's input directory with the corresponding VASP input reference.

1. Call `start_managed_vasp_sandbox` with the prepared workspace-relative
   `input_path`, the VASP `command` or its prepared `run.sh`, and the sandbox
   `template`. Supply `project_id` if needed and list the exact output filenames
   the user needs in `out_files`. If the session already tracks a suitable
   sandbox, pass its `job_id` instead of the template.
2. If MatCreator presents reusable records, wait for the user's choice. If it
   returns a running command, record the `job_id` and call
   `poll_remote_job_command`. Keep the same ID when resuming this step.
3. When the command ends, call `collect_remote_job_outputs` with the same
   `job_id` and the workspace output directory. The collector transfers files
   directly, checks the run receipt and VASP result, and archives verified data.
   Report the calculation, collection and archive outcomes independently.
   If a required transfer failed, retry collection on the same job while the
   sandbox is still reachable. A failed command can still have useful artifacts.
4. Release the sandbox with `terminate_remote_job` after collection. A database
   failure can be retried from previously transferred files.

## Preparing the command

For the `vasp-544-vtst` template, the Intel oneAPI environment supplies MPI, MKL
and the VASP executable. Use the allocated core count. A typical `run.sh` for
a 32-core sandbox contains the following:

```bash
#!/bin/bash
source /opt/intel/oneapi/setvars.sh
mpirun -np 32 vasp_std > log 2> err
```

Keep the script with the prepared input files and pass `command="bash run.sh"`.
The tool snapshots the prepared inputs and transfers the files needed by the
calculation. It automatically requests `vasprun.xml`, `OUTCAR` and the private
run receipt. Include other deliverables such as `CONTCAR`, `OSZICAR`, `log` or
`err` in `out_files` when needed.
