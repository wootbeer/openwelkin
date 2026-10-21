# Capture the MarsWar window to a PNG (default: server\snap.png) using PrintWindow.
param([string]$Out = (Join-Path $PSScriptRoot "..\server\snap.png"))
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System; using System.Runtime.InteropServices;
public class MWSnap { [StructLayout(LayoutKind.Sequential)] public struct R { public int L,T,Ri,B; }
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out R r);
[DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint f); }
'@
$h = (Get-Process MarsWar).MainWindowHandle
$r = New-Object MWSnap+R
[MWSnap]::GetWindowRect($h, [ref]$r) | Out-Null
$bmp = New-Object System.Drawing.Bitmap ($r.Ri - $r.L), ($r.B - $r.T)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$dc = $g.GetHdc()
[MWSnap]::PrintWindow($h, $dc, 2) | Out-Null
$g.ReleaseHdc($dc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
"saved $Out"
