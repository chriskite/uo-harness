# Captures the UO game window (not the Razor window) of the ClassicUO process.
param([string]$Out = 'C:\Users\chris\uo-harness\screen_game.png')
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Collections.Generic;
public class Win32 {
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdcBlt, uint nFlags);
    [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr hwnd, out RECT lpRect);
    [DllImport("user32.dll")] public static extern bool EnumThreadWindows(uint dwThreadId, EnumWindowsProc lpfn, IntPtr lParam);
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    public struct RECT { public int Left, Top, Right, Bottom; }
    public static List<IntPtr> GetThreadWindows(uint threadId) {
        var list = new List<IntPtr>();
        EnumThreadWindows(threadId, (h, l) => { list.Add(h); return true; }, IntPtr.Zero);
        return list;
    }
}
"@
$p = Get-Process ClassicUO -ErrorAction Stop
$best = $null; $bestArea = 0
foreach ($t in $p.Threads) {
    foreach ($h in [Win32]::GetThreadWindows($t.Id)) {
        $sb = New-Object System.Text.StringBuilder 256
        [Win32]::GetWindowText($h, $sb, 256) | Out-Null
        $r = New-Object Win32+RECT
        [Win32]::GetClientRect($h, [ref]$r) | Out-Null
        $area = ($r.Right - $r.Left) * ($r.Bottom - $r.Top)
        $vis = [Win32]::IsWindowVisible($h)
        Write-Output ("  window {0} '{1}' {2}x{3} visible={4}" -f $h, $sb.ToString(), ($r.Right-$r.Left), ($r.Bottom-$r.Top), $vis)
        if ($vis -and $area -gt $bestArea) { $bestArea = $area; $best = $h }
    }
}
if (-not $best) { Write-Output "no visible window found"; exit 1 }
Write-Output "capturing biggest visible window: $best (${bestArea}px)"
$r = New-Object Win32+RECT
[Win32]::GetClientRect($best, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap($w, $h)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
[Win32]::PrintWindow($best, $hdc, 2) | Out-Null
$g.ReleaseHdc($hdc)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $Out (${w}x${h})"
