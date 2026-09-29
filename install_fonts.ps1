<#
  安裝 Chiron GoRound TC 字型（僅安裝給目前使用者，不需要管理員權限）。

  - 已經有安裝就跳過，不重複處理
  - 字型檔會複製到 %LOCALAPPDATA%\Microsoft\Windows\Fonts
  - 在 HKCU\...\CurrentVersion\Fonts 建立登錄項目
  - 廣播 WM_FONTCHANGE，讓「已經開著」的程式立刻套用新字型（不必重開機）

  用法： powershell -ExecutionPolicy Bypass -File install_fonts.ps1
        powershell -ExecutionPolicy Bypass -File install_fonts.ps1 -Force   （強制重裝）
#>
param([switch]$Force)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$fontDir   = Join-Path $scriptDir "fonts"

# 檔案 -> Windows 登錄用的字型顯示名稱
#   命名規則：name ID 1（家族）+ 若 name ID 2 是 Bold 就加 " Bold" + " (TrueType)"
$map = [ordered]@{
    "ChironGoRoundTC-400R.ttf" = "Chiron GoRound TC (TrueType)"
    "ChironGoRoundTC-700B.ttf" = "Chiron GoRound TC Bold (TrueType)"
    "ChironGoRoundTC-500M.ttf" = "Chiron GoRound TC M (TrueType)"
    "ChironGoRoundTC-600SB.ttf" = "Chiron GoRound TC SB (TrueType)"
}

$regKey = "HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"
$destDir = Join-Path $env:LOCALAPPDATA "Microsoft\Windows\Fonts"

Write-Host ""
Write-Host "=== Chiron GoRound TC 字型安裝 ===" -ForegroundColor Cyan

if (-not (Test-Path $fontDir)) {
    Write-Host "！找不到字型資料夾：$fontDir" -ForegroundColor Yellow
    Write-Host "  程式仍可運作，只會改用系統預設字型（介面不會壞掉）。" -ForegroundColor Yellow
    exit 0
}

# ---- 先檢查是不是已經裝好了 ----
$already = $true
foreach ($name in $map.Values) {
    if (-not (Get-ItemProperty -Path $regKey -Name $name -ErrorAction SilentlyContinue)) {
        $already = $false
        break
    }
}
if ($already -and -not $Force) {
    Write-Host "字型已經安裝好了，略過。（要強制重裝請加 -Force）" -ForegroundColor Green
    exit 0
}
if ($Force) {
    Write-Host "-Force：準備重新安裝" -ForegroundColor Yellow
}

# ---- 複製檔案 ----
if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Force -Path $destDir | Out-Null }

# 注意：字型檔有可能已經被系統或其他程式載入而「被鎖住」，
#       Copy-Item 會直接拋錯。這裡逐檔 try/catch，而且大小相同就跳過不重複複製。
$ready = @{}
foreach ($file in $map.Keys) {
    $src = Join-Path $fontDir $file
    $dst = Join-Path $destDir $file
    if (-not (Test-Path $src)) {
        Write-Host "  略過（來源檔不存在）：$file" -ForegroundColor DarkGray
        continue
    }
    if (Test-Path $dst) {
        if ((Get-Item $dst).Length -eq (Get-Item $src).Length) {
            Write-Host "  已存在且相同，略過複製：$file" -ForegroundColor DarkGray
            $ready[$file] = $true
            continue
        }
    }
    try {
        Copy-Item -Path $src -Destination $dst -Force -ErrorAction Stop
        Write-Host "  已複製：$file" -ForegroundColor DarkGray
        $ready[$file] = $true
    } catch {
        # 被鎖住通常是因為字體已在系統中，這時檔案本身已經可用，繼續往下走
        if (Test-Path $dst) {
            Write-Host "  檔案被鎖住但已存在，繼續使用：$file" -ForegroundColor DarkYellow
            $ready[$file] = $true
        } else {
            Write-Host "  複製失敗：$file （$($_.Exception.Message)）" -ForegroundColor Red
            $ready[$file] = $false
        }
    }
}

# ---- 寫登錄 ----
if (-not (Test-Path $regKey)) { New-Item -Path $regKey -Force | Out-Null }
$registered = @()
foreach ($file in $map.Keys) {
    if (-not $ready[$file]) { continue }
    $regName = $map[$file]
    $full    = Join-Path $destDir $file
    New-ItemProperty -Path $regKey -Name $regName -Value $full -PropertyType String -Force | Out-Null
    Write-Host "  已註冊：$regName" -ForegroundColor DarkGray
    $registered += $regName
}

# ---- 讓字型立刻生效（廣播 WM_FONTCHANGE）----
try {
    Add-Type -Namespace Win -Name Font -MemberDefinition @"
[DllImport("gdi32.dll", CharSet=CharSet.Unicode)]
public static extern int AddFontResource(string lp);
[DllImport("user32.dll", CharSet=CharSet.Unicode)]
public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, IntPtr wParam,
    IntPtr lParam, uint fuFlags, uint uTimeout, out IntPtr lpdwResult);
"@
    $HWND_BROADCAST = [IntPtr]0xffff
    $WM_FONTCHANGE  = 0x001D
    $SMTO_ABORTIFHUNG = 0x0002
    foreach ($file in $map.Keys) {
        if ($ready[$file]) {
            [void][Win.Font]::AddFontResource((Join-Path $destDir $file))
        }
    }
    $r = [IntPtr]::Zero
    [void][Win.Font]::SendMessageTimeout($HWND_BROADCAST, $WM_FONTCHANGE,
            [IntPtr]::Zero, [IntPtr]::Zero, $SMTO_ABORTIFHUNG, 2000, [ref]$r)
    Write-Host "  已廣播 WM_FONTCHANGE，開著的程式會立刻套用" -ForegroundColor DarkGray
} catch {
    Write-Host "  （即時生效的廣播略過，重新開啟程式即可套用）" -ForegroundColor DarkGray
}

# ---- 驗證 ----
$ok = 0
foreach ($name in $map.Values) {
    if (Get-ItemProperty -Path $regKey -Name $name -ErrorAction SilentlyContinue) { $ok++ }
}
$total = $map.Count
$ErrorActionPreference = "Continue"

Write-Host ""
if ($ok -eq $total) {
    Write-Host "✅ 字型安裝完成（$ok/$total），介面會用 Chiron GoRound TC" -ForegroundColor Green
} elseif ($ok -gt 0) {
    Write-Host "⚠ 部分安裝成功（$ok/$total），介面會退回系統字型" -ForegroundColor Yellow
} else {
    Write-Host "⚠ 字型安裝失敗，介面會自動退回系統繁中字型（不影響功能）" -ForegroundColor Yellow
}
Write-Host "  授權：SIL Open Font License 1.1（見 fonts\LICENSE-OFL.txt）"
Write-Host ""
exit 0
