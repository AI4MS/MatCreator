# Sandbox files

Upload and download files and directories.

## Uploading files and directories

`files write --source` accepts either a single file or a directory. When
given a directory, every regular file under it is uploaded in one batch
with relative paths preserved under the remote root. For very large
trees, tar locally then untar inside the sandbox via `exec`.

```bash
lbg sdbx files write --source ./run.py <sandbox_id> /workspace/run.py --json
lbg sdbx files write --source ./project <sandbox_id> /workspace/project --json
```

## Fast uploads to /bohr-workspace (tiefblue, `--ti`)

`/bohr-workspace` is not a sandbox-local disk — it is a **persistent,
session-scoped directory backed by object storage (tiefblue)**. Files written
there survive `kill` and are visible to any sandbox created with the same
session id. Pass `--ti` to upload a file/directory **straight to that object
storage** instead of streaming it through the sandbox filesystem: this is much
faster for large files (automatic multipart) and skips the sandbox hop.

```bash
# a single file
lbg sdbx files write --ti --session-id <session-id> --source ./model.pt \
  <sandbox_id> /bohr-workspace/model.pt --json
# a whole directory
lbg sdbx files write --ti --session-id <session-id> --source ./out_dir \
  <sandbox_id> /bohr-workspace/out --json
```

Rules and caveats:

- **`--session-id` is required** and must match the id the sandbox was created
  with (`lbg sdbx create --session-id <id>`). `/bohr-workspace` is bound to the
  session, so the token is scoped per (user, session).
- Only paths under **`/bohr-workspace`** (or its sub-directories) are supported.
  For any other path, upload without `--ti` (via the sandbox filesystem).
- `--content` is not supported with `--ti`; pass a real `--source` file or dir.
- **Directory uploads are recursive and have no dry-run or confirmation** — every
  file under `--source` (including hidden files, `.git/`, `node_modules/`, and any
  secrets like `.env`) is pushed and overwrites existing remote objects. Double-check
  the source path and prune unwanted files first; for big/messy trees prefer
  `tar` + `exec` instead.
- The `--json` result reports `object_key` (object-storage key), `sandbox_path`
  (where it appears inside the sandbox), `host`, and `bytes` per file.
- Files uploaded via `--ti` appear inside the sandbox owned by `root:root` with
  mode `640`, so a non-root sandbox user (e.g. uid 1001) cannot read them
  directly — read them with `exec --user root`, or `chown`/`chmod` as root first.
- Other object-storage mounts may live on different tiefblue endpoints and are
  not wired to `--ti` yet.

## Downloading files

`files read` prints a file to stdout, or writes it to local disk with
`--output`.

```bash
lbg sdbx files read <sandbox_id> /workspace/result.csv                       # print text to stdout
lbg sdbx files read <sandbox_id> /workspace/result.csv --output ./result.csv  # save to disk
lbg sdbx files read <sandbox_id> /workspace/model.pt   --output ./model.pt    # binary, auto-detected
```

### Text vs bytes (important for binary files)

When `--format` is omitted it is **auto-detected from the file extension**:
known binary types (`.pt`, `.pth`, `.bin`, `.ckpt`, `.safetensors`, `.npy`,
`.tar`, `.gz`, `.zip`, `.parquet`, `.pdf`, `.so`, images, ...) are read as
**bytes**, everything else as **text**. A `binary file detected ...` warning
is printed to stderr when this happens.

Reading a binary file as `text` runs it through a charset decode + UTF-8
re-encode roundtrip that **silently corrupts the bytes** (wrong size,
`torch.load` / unzip fails with "invalid header or archive is corrupted").
The auto-detection prevents this for known extensions; for an unrecognized
extension on a binary file, pass `--format bytes` explicitly:

```bash
lbg sdbx files read <sandbox_id> /workspace/checkpoint.weights --format bytes --output ./checkpoint.weights
```

Force `--format text` only when you knowingly want the decoded string of a
binary file (rare).
