# ============================================================
# Sanjin Zhixing -- generate chunked clipboard upload kit
#
# PURPOSE
#   When your local machine has no public IP (so the server cannot
#   pull files from it), transfer sjx-deploy.zip through the Aliyun
#   VNC console clipboard.
#
#   zip -> Base64 -> split into chunks -> paste one by one ->
#   server joins them back -> restore the zip.
#   Chunking avoids a single ~625 KB paste being truncated by VNC.
#
# USAGE (local PowerShell)
#   cd <项目根目录>
#   powershell -ExecutionPolicy Bypass -File scripts\make_clipboard_upload.ps1
#
# OUTPUT: C:\sanjin-upload\
#   step1.ps1                    run first on the server
#   step2_chunkNNN.ps1           paste these one at a time
#   step3.ps1                    restore + verify + extract (run last)
#   README.txt                   step-by-step instructions
#
# NOTE: this file is intentionally pure ASCII so that Windows
#       PowerShell 5.1 parses it identically with or without a BOM.
#       The files it GENERATES contain Chinese and are written with
#       a UTF-8 BOM so PowerShell 5.1 displays them correctly.
# ============================================================

[CmdletBinding()]
param(
    # Project root. Empty = infer from this script's location.
    [string]$ProjectRoot = "",
    [string]$ZipName = "sjx-deploy.zip",
    [string]$OutDir = "C:\sanjin-upload",
    # Characters per chunk. Shorter = safer paste, but more chunks.
    [int]$ChunkSize = 8000
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $ProjectRoot = Split-Path -Parent $PSScriptRoot
    } else {
        $ProjectRoot = (Get-Location).Path
    }
}
if (-not (Test-Path $ProjectRoot)) {
    Write-Host "[!] Project root not found: $ProjectRoot" -ForegroundColor Red
    exit 1
}

$zip = Join-Path $ProjectRoot $ZipName
if (-not (Test-Path $zip)) {
    Write-Host "[!] Not found: $zip" -ForegroundColor Red
    Write-Host "    Run first: cd backend; python scripts\make_deploy_package.py" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "=== Building chunked clipboard upload kit ===" -ForegroundColor Cyan
Write-Host "Project root : $ProjectRoot"
Write-Host "Source file  : $zip"

$bytes = [IO.File]::ReadAllBytes($zip)
$b64 = [Convert]::ToBase64String($bytes)
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash
$sizeKB = [math]::Round($bytes.Length / 1KB)

Write-Host "Size         : $sizeKB KB"
Write-Host "Base64 length: $($b64.Length) chars"

$chunks = @()
for ($i = 0; $i -lt $b64.Length; $i += $ChunkSize) {
    $chunks += $b64.Substring($i, [Math]::Min($ChunkSize, $b64.Length - $i))
}
$total = $chunks.Count
Write-Host "Chunks       : $total x <=$ChunkSize chars"
Write-Host "SHA256       : $sha"
Write-Host ""

if (Test-Path $OutDir) { Remove-Item $OutDir -Recurse -Force }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

# Generated files are read by PowerShell 5.1 on the server -> need BOM.
$encBom = [System.Text.UTF8Encoding]::new($true)

# ---------- step1 ----------
$step1 = @"
# ===== 第 1 步：在服务器上执行（初始化）=====
# 预期块数：$total
# 原始文件：$ZipName  ($sizeKB KB)
# SHA256  ：$sha

`$global:SJX_CHUNKS = New-Object System.Collections.Generic.List[string]
`$global:SJX_EXPECT = $total
Write-Host "已初始化，等待接收 $total 个分块。" -ForegroundColor Green
Write-Host "接下来依次粘贴执行 step2_chunk*.ps1 的内容。"
"@
[IO.File]::WriteAllText((Join-Path $OutDir 'step1.ps1'), $step1, $encBom)

# ---------- step2 (one file per chunk) ----------
for ($i = 0; $i -lt $total; $i++) {
    $n = $i + 1
    $name = "step2_chunk{0:D3}.ps1" -f $n
    $body = @"
# ===== 第 2 步 · 分块 $n / $total =====
`$global:SJX_CHUNKS.Add('$($chunks[$i])')
Write-Host "[$n/$total] 已接收，累计 `$(`$global:SJX_CHUNKS.Count) 块"
"@
    [IO.File]::WriteAllText((Join-Path $OutDir $name), $body, $encBom)
}

# ---------- step3 ----------
$step3 = @"
# ===== 第 3 步：还原 + 校验 + 解压 =====
`$expect = $total
`$sha    = "$sha"
`$name   = "$ZipName"

if (`$global:SJX_CHUNKS.Count -ne `$expect) {
    Write-Host "!! 分块数量不对：收到 `$(`$global:SJX_CHUNKS.Count) / 应有 `$expect" -ForegroundColor Red
    Write-Host "   请检查是否有分块没粘贴成功，必要时从 step1 重新开始。" -ForegroundColor Yellow
    return
}

`$b64 = (`$global:SJX_CHUNKS -join '').Trim()
Write-Host "拼接后 Base64 长度: `$(`$b64.Length)"

New-Item -ItemType Directory -Path C:\sanjin -Force | Out-Null
`$out = "C:\sanjin\`$name"
[IO.File]::WriteAllBytes(`$out, [Convert]::FromBase64String(`$b64))

`$got = (Get-FileHash `$out -Algorithm SHA256).Hash
Write-Host ""
Write-Host "期望 SHA256: `$sha"
Write-Host "实际 SHA256: `$got"
if (`$got -eq `$sha) {
    Write-Host "[OK] SHA256 一致，文件完整！" -ForegroundColor Green
    Write-Host "大小: `$([math]::Round((Get-Item `$out).Length/1KB)) KB"
    Write-Host ""
    Write-Host "--- 正在解压到 C:\sanjin ---"
    cd C:\sanjin
    Expand-Archive -Path `$out -DestinationPath . -Force
    Write-Host "[OK] 解压完成，目录内容：" -ForegroundColor Green
    Get-ChildItem C:\sanjin | ForEach-Object { Write-Host "  `$(`$_.Name)" }
} else {
    Write-Host "[FAIL] SHA256 不一致！文件在传输中损坏。" -ForegroundColor Red
    Write-Host "       多半是某个分块被 VNC 截断了，请重新粘贴出错的分块。" -ForegroundColor Yellow
}
"@
[IO.File]::WriteAllText((Join-Path $OutDir 'step3.ps1'), $step3, $encBom)

# ---------- README ----------
$lastChunk = "step2_chunk{0:D3}.ps1" -f $total
$readme = @"
三晋智行 · 分块剪贴板上传操作说明
========================================

源文件 : $ZipName  ($sizeKB KB)
分块数 : $total
SHA256 : $sha

操作顺序
--------
【服务器上】打开 VNC 控制台 → 进 PowerShell（管理员）

1. 打开 step1.ps1，全选内容 → 复制 → 粘贴到服务器 PowerShell → 回车

2. 依次打开 step2_chunk001.ps1 ~ $lastChunk
   每个文件：全选 → 复制 → 粘贴到服务器 → 回车
   每粘贴一块，服务器会打印 [n/$total] 已接收

3. 全部粘贴完后，打开 step3.ps1 → 复制 → 粘贴 → 回车
   它会自动校验 SHA256 并解压到 C:\sanjin

注意事项
--------
- 每块粘贴后必须按回车执行，再粘下一块
- 如果某块粘贴后报错或没打印「已接收」，重粘那一块即可
- 中途断了不要紧，重新从 step1 开始即可

省事替代方案
------------
嫌 $total 块太多的话，可以加大块来减少块数：
  powershell -ExecutionPolicy Bypass -File scripts\make_clipboard_upload.ps1 -ChunkSize 30000
块数会少很多，但单块被截断的风险更高。
"@
[IO.File]::WriteAllText((Join-Path $OutDir 'README.txt'), $readme, $encBom)

Write-Host "Generated into $OutDir" -ForegroundColor Green
Write-Host ""
Write-Host "  step1.ps1                 init (run first)"
Write-Host "  step2_chunk001.ps1 ...    paste one by one ($total chunks)"
Write-Host "  step3.ps1                 restore + verify + extract (run last)"
Write-Host "  README.txt                instructions"
Write-Host ""
Write-Host "Fewer chunks? Raise ChunkSize:"
Write-Host "  -ChunkSize 30000  -> about $([math]::Ceiling($b64.Length / 30000)) chunks"
Write-Host "  -ChunkSize 50000  -> about $([math]::Ceiling($b64.Length / 50000)) chunks" -ForegroundColor DarkGray
Write-Host ""
