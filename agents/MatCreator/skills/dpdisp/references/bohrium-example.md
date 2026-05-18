# Bohrium (DP Cloud) Example

When `context_type` is `BohriumContext`, the `remote_profile` uses an `input_data` sub-object specifying the container image, machine type, and platform. Authenticate with `email`/`password`/`program_id` (always from environment variables).

> **Always use `--with oss2`** for Bohrium jobs. `oss2` (Aliyun OSS SDK) is required by `BohriumContext` for file upload but is not bundled with dpdispatcher in uvx isolated environments.

## Key `input_data` fields

| Field | Description | Example |
|---|---|---|
| `job_type` | Must be `"container"` for image-based jobs | `"container"` |
| `scass_type` | Machine spec (CPU/GPU/memory) | `"c16_m32_cpu"`, `"c32_m128_cpu"`, `"gpu_4_v100_32g"` |
| `image_name` | Full container image URI | `"registry.dp.tech/dptech/prod-15454/vasp:5.4.4"` |
| `platform` | Cloud platform | `"ali"` (Alibaba Cloud) |
| `log_file` | Path for stdout inside container | `"log"` |

## `submission.template.json`

```json
{
  "work_base": "<work_dir_root>",
  "machine": {
    "batch_type": "Bohrium",
    "context_type": "BohriumContext",
    "local_root": ".",
    "remote_profile": {
      "email": "${BOHRIUM_EMAIL}",
      "password": "${BOHRIUM_PASSWORD}",
      "program_id": ${BOHRIUM_PROJECT_ID},
      "input_data": {
        "job_type": "container",
        "log_file": "log",
        "scass_type": "${BOHRIUM_MACHINE_TYPE}",
        "platform": "ali",
        "image_name": "${BOHRIUM_IMAGE}"
      }
    }
  },
  "resources": {
    "group_size": 1
  },
  "task_list": [
    {
      "command": "bash run.sh",
      "task_work_path": "<task_dir>",
      "forward_files": ["run.sh", "input.dat"],
      "backward_files": ["result.out", "log", "err"]
    }
  ]
}
```

## Commands

```bash
envsubst '${BOHRIUM_EMAIL} ${BOHRIUM_PASSWORD} ${BOHRIUM_PROJECT_ID} ${BOHRIUM_MACHINE_TYPE} ${BOHRIUM_IMAGE}' \
  < submission.template.json > submission.json
uv run -m json.tool submission.json >/dev/null
uvx --from dpdispatcher --with oss2 dpdisp submit submission.json
```
