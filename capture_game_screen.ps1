# Brings the game window to front and captures its screen region via BitBlt.
param([string]$Out = (Join-Path $PSScriptRoot 'screen_game.png'))
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32V {
    [DllImport("user32.dll")] public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
    public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
$hwnd = (Get-Process ClassicUO -ErrorAction Stop).MainWindowHandle
if ($hwnd -eq [IntPtr]::Zero) { Write-Output "game window not found"; exit 1 }
[Win32V]::ShowWindow($hwnd, 9) | Out-Null   # SW_RESTORE
[Win32V]::SetForegroundWindow($hwnd) | Out-Null
Start-Sleep -Milliseconds 700
$r = New-Object Win32V+RECT
[Win32V]::GetWindowRect($hwnd, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $Out (${w}x${h})"
