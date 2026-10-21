# Launch the MarsWar client directly (skipping the dead Loader.exe updater) and park its window at the top-left.
param([string]$Client = $(if ($env:MW_CLIENT_DIR) { $env:MW_CLIENT_DIR } else { "C:\Games\MarsWar" }))
$p = Start-Process -FilePath (Join-Path $Client "System\MarsWar.exe") -WorkingDirectory (Join-Path $Client "System") -PassThru
Add-Type @'
using System; using System.Runtime.InteropServices;
public class MWWin { [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr a, int x, int y, int cx, int cy, uint f);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }
'@
for ($i = 0; $i -lt 60 -and $p.MainWindowHandle -eq [IntPtr]::Zero; $i++) { Start-Sleep -Milliseconds 500; $p.Refresh() }
Start-Sleep 8
$p.Refresh()
[MWWin]::SetWindowPos($p.MainWindowHandle, [IntPtr]::Zero, 0, 0, 0, 0, 0x0001) | Out-Null
[MWWin]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
"pid $($p.Id)"
