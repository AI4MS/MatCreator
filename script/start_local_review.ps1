param(
    [switch]$BackendOnly,
    [switch]$WebOnly,
    [switch]$TokenWorker,
    [ValidateSet('process','srt')]
    [string]$RuntimeBackend = 'process',
    [string]$SrtCommand = '',
    [Alias('InstanceHome')]
    [string]$RuntimeHome = '',
    [string]$Workspace = '',
    [ValidateRange(1,65535)][int]$AdkPort = 18000,
    [ValidateRange(1,65535)][int]$WebPort = 18001,
    [ValidateRange(1,65535)][int]$FrontendPort = 15173,
    [string]$LogDirectory = '',
    [string]$PythonPath = '',
    [string]$NodePath = ''
)
$ErrorActionPreference = 'Stop'
$reviewRoot = Split-Path $PSScriptRoot -Parent
if (-not $RuntimeHome) { $RuntimeHome = Join-Path $reviewRoot '.local-runtime/home' }
$RuntimeHome = [IO.Path]::GetFullPath($RuntimeHome)
if (-not $Workspace) { $Workspace = Join-Path $RuntimeHome 'workspace' }
$Workspace = [IO.Path]::GetFullPath($Workspace)
if (-not $LogDirectory) { $LogDirectory = Join-Path $RuntimeHome 'logs' }
$reviewLogs = [IO.Path]::GetFullPath($LogDirectory)

function Resolve-ReviewExecutable([string]$Requested,[string[]]$Candidates,[string]$Command,[string]$Label) {
    if ($Requested) {
        if (Test-Path -LiteralPath $Requested -PathType Leaf) { return [IO.Path]::GetFullPath($Requested) }
        $reviewCommand = Get-Command -Name $Requested -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($reviewCommand) { return $reviewCommand.Source }
        throw "$Label not found: $Requested"
    }
    foreach ($reviewCandidate in $Candidates) {
        if (Test-Path -LiteralPath $reviewCandidate -PathType Leaf) { return [IO.Path]::GetFullPath($reviewCandidate) }
    }
    $reviewCommand = Get-Command -Name $Command -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($reviewCommand) { return $reviewCommand.Source }
    throw "$Label not found. Install it or provide its executable path."
}

$reviewTokenWorker = $TokenWorker -or $env:MATCREATOR_LOCAL_PROFILE_WORKER -eq '1'
if ($reviewTokenWorker -and $RuntimeBackend -ne 'process') { throw 'An isolated runtime must be started by its account broker.' }
$reviewConfiguredPorts = @($WebPort,$FrontendPort)
if ($reviewTokenWorker) { $reviewConfiguredPorts += $AdkPort }
if (@($reviewConfiguredPorts | Select-Object -Unique).Count -ne $reviewConfiguredPorts.Count) { throw 'Active services need distinct ports.' }
$PythonPath = Resolve-ReviewExecutable $PythonPath @((Join-Path $reviewRoot '.venv/Scripts/python.exe'),(Join-Path $reviewRoot '.venv/bin/python')) 'python' 'Python'
& $PythonPath -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)'
if ($LASTEXITCODE -ne 0) { throw 'MC requires Python 3.12 or newer. Select it with -PythonPath.' }
if (-not $BackendOnly) {
    $NodePath = Resolve-ReviewExecutable $NodePath @() 'node' 'Node'
    $reviewNodeEngine = (Get-Content -Raw -LiteralPath (Join-Path $reviewRoot 'web/vite-frontend/package.json') | ConvertFrom-Json).engines.node
    $reviewNodeVersion = & $NodePath --version
    if ($LASTEXITCODE -ne 0 -or $reviewNodeEngine -notmatch '^>=(\d+\.\d+\.\d+)$') { throw 'Cannot verify the frontend Node requirement.' }
    $reviewMinimumNode = [version]$Matches[1]
    if ($reviewNodeVersion -notmatch '^v(\d+\.\d+\.\d+)') { throw 'Cannot verify the selected Node executable version.' }
    if ([version]$Matches[1] -lt $reviewMinimumNode) { throw "Frontend requires Node $reviewNodeEngine. Select it with -NodePath." }
}
# Never attach a fresh frontend to an unknown existing backend.
$reviewPorts = @($WebPort)
if ($reviewTokenWorker -and -not $WebOnly) { $reviewPorts += $AdkPort }
if (-not $BackendOnly) { $reviewPorts += $FrontendPort }
foreach ($reviewPort in $reviewPorts) {
    $reviewListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,$reviewPort)
    $reviewListener.ExclusiveAddressUse = $true
    try { $reviewListener.Start() }
    catch { throw "Port $reviewPort is occupied. No existing service was reused." }
    finally { $reviewListener.Stop() }
}
New-Item -ItemType Directory -Force -Path $reviewLogs | Out-Null
$env:PYTHONPATH = (Join-Path $reviewRoot 'src') + [IO.Path]::PathSeparator + $reviewRoot
$env:MATCREATOR_MODE = 'local'
$env:MATCREATOR_LOCAL_RUNTIME = $RuntimeBackend
if ($SrtCommand) { $env:MATCREATOR_LOCAL_SRT_COMMAND = $SrtCommand }
$env:MATCREATOR_LOCAL_PROFILE_WORKER = if($reviewTokenWorker){'1'}else{'0'}
if (-not $reviewTokenWorker) {
    Remove-Item Env:MATCREATOR_LOCAL_SRT_WORKER -ErrorAction SilentlyContinue
    Remove-Item Env:MATCREATOR_LOCAL_ACCOUNT_ROOT -ErrorAction SilentlyContinue
    Remove-Item Env:MATCREATOR_LOCAL_ACCOUNT_ID -ErrorAction SilentlyContinue
}
$env:MATCREATOR_HOME = $RuntimeHome
$env:MATCREATOR_CONFIG_PATH = Join-Path $RuntimeHome 'config.yaml'
$env:MATCLAW_WORKSPACE = $Workspace
$env:MATCREATOR_ADK_PORT = [string]$AdkPort
$env:ADK_LOCAL_PORT = [string]$AdkPort
$env:MATCREATOR_WEB_PORT = [string]$WebPort
$env:MATCREATOR_FRONTEND_PORT = [string]$FrontendPort
$env:MATCREATOR_WEB_TARGET = "http://127.0.0.1:$WebPort"
if (-not $BackendOnly) { $env:Path = (Split-Path $NodePath -Parent) + [IO.Path]::PathSeparator + $env:Path }
# Initialize identity/token without printing the secret value.
$reviewIdentityJson = & $PythonPath -c 'import json,os; from pathlib import Path; from matcreator.local_access import ensure_local_access; a=ensure_local_access(Path(os.environ["MATCREATOR_HOME"])); print(json.dumps({"instance_id":a.instance_id,"browser_host":a.browser_host,"token_path":str(a.token_path)}))'
if ($LASTEXITCODE -ne 0) { throw 'Failed to initialize private local instance.' }
$reviewIdentity = $reviewIdentityJson | ConvertFrom-Json
if (-not $reviewTokenWorker -and $RuntimeBackend -ne 'process') {
    & $PythonPath -c 'import os,sys; from pathlib import Path; from matcreator.local_account_backends import create_local_runtime_manager; create_local_runtime_manager(Path(os.environ["MATCREATOR_HOME"]),Path(sys.argv[1])).preflight()' $reviewRoot
    if ($LASTEXITCODE -ne 0) { throw 'Isolated runtime preflight failed. No process fallback or account worker was started.' }
}
if ($reviewTokenWorker) {
    if (-not $env:MATCREATOR_LOCAL_ACCOUNT_ROOT) { $env:MATCREATOR_LOCAL_ACCOUNT_ROOT = $RuntimeHome }
    if (-not $env:MATCREATOR_LOCAL_ACCOUNT_ID) { $env:MATCREATOR_LOCAL_ACCOUNT_ID = [Guid]::ParseExact($reviewIdentity.instance_id,'N').ToString('D') }
}
function ConvertTo-ReviewArgument([string]$Value) {
    if ($Value -match '[\s"]') { return '"' + ($Value -replace '"','\"') + '"' }
    return $Value
}
function Start-ReviewProcess([string]$Executable,[string[]]$Arguments,[string]$WorkingDirectory,[string]$Name) {
    $reviewArgs = $Arguments | ForEach-Object { ConvertTo-ReviewArgument $_ }
    Start-Process $Executable -ArgumentList $reviewArgs -WorkingDirectory $WorkingDirectory -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $reviewLogs "$Name.out.log") `
        -RedirectStandardError (Join-Path $reviewLogs "$Name.err.log") -PassThru
}
$adk = $null; $web = $null; $vite = $null
try {
    if ($reviewTokenWorker -and -not $WebOnly) { $adk = Start-ReviewProcess $PythonPath @('-m','matcreator.scripts.start_agent','api-server','--host','127.0.0.1','--port',[string]$AdkPort,'--workspace',$Workspace,'--log-level','info') $reviewRoot 'adk' }
    $web = Start-ReviewProcess $PythonPath @('-m','uvicorn','web.main:app','--host','127.0.0.1','--port',[string]$WebPort) $reviewRoot 'web'
    if (-not $BackendOnly) { $vite = Start-ReviewProcess $NodePath @('node_modules/vite/bin/vite.js','--host','127.0.0.1','--port',[string]$FrontendPort,'--strictPort') (Join-Path $reviewRoot 'web/vite-frontend') 'vite' }
} catch {
    foreach ($reviewProcess in @($adk,$web,$vite)) {
        if ($null -ne $reviewProcess -and -not $reviewProcess.HasExited) { & taskkill.exe /PID $reviewProcess.Id /T /F 2>$null | Out-Null }
    }
    throw
}
$reviewReceipt = [ordered]@{
    instance_id=$reviewIdentity.instance_id; home=$RuntimeHome; workspace=$Workspace
    mode=if($reviewTokenWorker){'token-worker'}else{'local-accounts'}
    runtime_backend=$RuntimeBackend
    token_path=$reviewIdentity.token_path; source=$reviewRoot; started_at=[DateTimeOffset]::Now.ToString('o')
    frontend_url="http://$($reviewIdentity.browser_host):$FrontendPort/"
    adk_pid=if($adk){$adk.Id}else{$null}; web_pid=$web.Id; vite_pid=if($vite){$vite.Id}else{$null}
    adk_port=$AdkPort; web_port=$WebPort; frontend_port=$FrontendPort
}
$reviewReceipt | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $reviewLogs 'launch.json')
Write-Output "Started local instance $($reviewIdentity.instance_id): ADK=$($adk.Id), Web=$($web.Id), Vite=$($vite.Id)"
Write-Output "Data directory: $RuntimeHome"
Write-Output "Frontend: http://$($reviewIdentity.browser_host):$FrontendPort/"
if ($reviewTokenWorker) {
    Write-Output 'Internal token worker mode. Keep its private backend credentials out of user-facing logs.'
} else {
    Write-Output 'Register or sign in to a local account in the browser. Each account starts its own private backend.'
    Write-Output 'To claim existing installation data during setup, use matcreator access --url <frontend-url> for a private setup link.'
}
