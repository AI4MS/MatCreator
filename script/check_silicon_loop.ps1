param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CheckArgs)
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path (Join-Path $PSScriptRoot '..')).ProviderPath
if ($repoPath -notmatch '^\\\\wsl(?:\$|\.localhost)\\([^\\]+)\\(.+)$') {
    throw 'Run this wrapper from a WSL checkout; on Linux use python script/check_silicon_loop.py.'
}
$distribution = $Matches[1]
$linuxPath = '/' + $Matches[2].Replace('\', '/')
& wsl.exe -d $distribution --cd $linuxPath --exec ./.venv/bin/python script/check_silicon_loop.py @CheckArgs
exit $LASTEXITCODE
