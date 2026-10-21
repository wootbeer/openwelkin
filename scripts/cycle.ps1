# Restart the emulator (optionally with MW_SKIP) and relaunch the client, ready for the login click.
param([string]$Skip = "", [string]$Client = $(if ($env:MW_CLIENT_DIR) { $env:MW_CLIENT_DIR } else { "C:\Games\MarsWar" }))
$Server = Join-Path $PSScriptRoot "..\server"
Get-Process MarsWar -ErrorAction SilentlyContinue | Stop-Process -Force
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*loginserver.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
$env:MW_SKIP = $Skip
$env:MW_DEBUG = "1"  # dev runs keep packet traces
Start-Process -FilePath python -ArgumentList "-u", "loginserver.py" -WorkingDirectory $Server -WindowStyle Hidden
Start-Sleep 2
& (Join-Path $PSScriptRoot "launch.ps1") -Client $Client
