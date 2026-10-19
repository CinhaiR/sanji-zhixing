# ============================================================
# 三晋智行 —— 服务器敏感文件权限加固（Windows Server）
#
# 作用：把 backend\.env 与数据库文件的 NTFS 权限重写为
#       「SYSTEM + Administrators（+ 可选服务账户）」，
#       并断开权限继承，删掉其他所有账户的访问。
#
# 为什么需要这一步：
#   「.env 不打进部署包」只保证密钥不随压缩包传播。
#   文件一旦落到服务器磁盘，任何能登录、或能读到该目录的人
#   都能明文看到 API Key。收紧 ACL 才是服务器侧的实际动作。
#
# 实现说明：
#   用 SID 而不是账户名来授权，避免中文系统上
#   "Users" / "Authenticated Users" 这类英文别名匹配失败；
#   直接重写整份 ACL，而不是逐个 /remove，
#   否则以账户名单独出现的 ACE 会残留（实测过这个坑）。
#
# 用法（服务器上，管理员 PowerShell）：
#   cd C:\sanjin
#   powershell -ExecutionPolicy Bypass -File scripts\harden_env.ps1
#
#   若服务用低权限专用账户运行，必须带上该账户，否则服务读不到 .env：
#   powershell -ExecutionPolicy Bypass -File scripts\harden_env.ps1 -ServiceAccount "NT AUTHORITY\LocalService"
#
#   想把当前登录账户也保留为可读（方便自己维护）：
#   powershell -ExecutionPolicy Bypass -File scripts\harden_env.ps1 -KeepCurrentUser
# ============================================================

[CmdletBinding()]
param(
    # 项目根目录。留空则自动推断（脚本所在目录的上一级）。
    # 注意：不要在这里写 (Split-Path -Parent $PSScriptRoot) 作默认值 ——
    # 当脚本以 UTF-8 BOM 保存、又被 powershell.exe -File 调用时，
    # $PSScriptRoot 会取到空字符串，导致 Split-Path 报
    # "Cannot bind argument to parameter 'Path' because it is an empty string"。
    # 所以改为在运行时推断（见下方）。
    [string]$ProjectRoot = "",

    # 运行 uvicorn 服务的账户名。NSSM 默认以 LocalSystem 运行，
    # 它已在 Administrators 组内，无需指定。
    [string]$ServiceAccount = "",

    # 是否保留当前登录账户的读写权限
    [switch]$KeepCurrentUser
)

$ErrorActionPreference = "Stop"

# 运行时推断项目根目录（避开 $PSScriptRoot 在 BOM 场景下为空的问题）
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $ProjectRoot = Split-Path -Parent $PSScriptRoot
    } else {
        # $PSScriptRoot 不可用：用当前工作目录，并提示用户显式指定
        $ProjectRoot = (Get-Location).Path
        Write-Host "[提示] 无法自动定位脚本目录，已改用当前目录: $ProjectRoot" -ForegroundColor Yellow
        Write-Host "       若不对，请显式指定：-ProjectRoot C:\sanjin" -ForegroundColor Yellow
        Write-Host ""
    }
}
if (-not (Test-Path $ProjectRoot)) {
    Write-Host "[!] 项目根目录不存在: $ProjectRoot" -ForegroundColor Red
    exit 1
}

# 固定 SID：这些在任何语言的 Windows 上都一样
$SID_SYSTEM = "S-1-5-18"
$SID_ADMINS = "S-1-5-32-544"

$targets = @(
    (Join-Path $ProjectRoot "backend\.env"),
    (Join-Path $ProjectRoot "backend\.env.example"),
    (Join-Path $ProjectRoot "backend\app\sanji_zhixing.db"),
    (Join-Path $ProjectRoot "backend\sanji_zhixing.db")
)

Write-Host ""
Write-Host "=== 三晋智行 敏感文件权限加固 ===" -ForegroundColor Cyan
Write-Host "项目根目录: $ProjectRoot"
Write-Host ""

$found = @($targets | Where-Object { Test-Path -LiteralPath $_ })
if ($found.Count -eq 0) {
    Write-Host "[!] 未找到任何目标文件，请确认 -ProjectRoot 是否正确。" -ForegroundColor Yellow
    exit 1
}

# 组装保留清单
$grants = @(
    @{ Sid = $SID_SYSTEM; Rights = "Read, Write, Synchronize" }
    @{ Sid = $SID_ADMINS; Rights = "FullControl" }
)

if ($ServiceAccount -ne "") {
    try {
        $acct = New-Object System.Security.Principal.NTAccount($ServiceAccount)
        $sid  = $acct.Translate([System.Security.Principal.SecurityIdentifier]).Value
        $grants += @{ Sid = $sid; Rights = "Read, Synchronize" }
        Write-Host "已解析服务账户: $ServiceAccount -> $sid"
    } catch {
        Write-Host "[!] 无法解析账户 '$ServiceAccount'，请检查拼写。" -ForegroundColor Red
        exit 1
    }
}

if ($KeepCurrentUser) {
    $me = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $grants += @{ Sid = $me.User.Value; Rights = "Read, Write, Synchronize" }
    Write-Host "已保留当前账户: $($me.Name) -> $($me.User.Value)"
}

if ($ServiceAccount -ne "" -or $KeepCurrentUser) { Write-Host "" }

foreach ($file in $found) {
    Write-Host "--- 处理: $file" -ForegroundColor White

    # 1) 备份原 ACL 到文件旁，出问题可还原
    $backup = "$file.acl.bak"
    try {
        Get-Acl -LiteralPath $file | Export-Clixml -Path $backup -Depth 4
        Write-Host "    已备份原 ACL -> $backup"
    } catch {
        Write-Host "    (ACL 备份失败，继续: $($_.Exception.Message))" -ForegroundColor Yellow
    }

    # 2) 用 SID 授权，禁用继承并清空继承来的 ACE。
    #    注意：这里刻意不调用 SetOwner/SetGroup —— 改所有者需要
    #    SeRestorePrivilege，普通管理员令牌下会报
    #    「The security identifier is not allowed to be the owner of this object」。
    #    真正决定别人能不能读到密钥的是 DACL，重写它就够了。
    $acl = New-Object System.Security.AccessControl.FileSecurity
    $acl.SetAccessRuleProtection($true, $false)

    foreach ($g in $grants) {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            [System.Security.Principal.SecurityIdentifier]$g.Sid,
            [System.Security.AccessControl.FileSystemRights]$g.Rights,
            [System.Security.AccessControl.InheritanceFlags]::None,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
    }

    Set-Acl -LiteralPath $file -AclObject $acl
    Write-Host "    已重写 ACL：继承已断开，仅保留 $($grants.Count) 条授权"
    Write-Host ""
}

Write-Host "=== 加固后实际权限 ===" -ForegroundColor Cyan
foreach ($file in $found) {
    Write-Host ""
    Write-Host $file -ForegroundColor White
    $a = Get-Acl -LiteralPath $file
    if ($a.AreAccessRulesProtected) { $inh = "已断开" } else { $inh = "仍启用(异常)" }
    Write-Host ("    继承: " + $inh)
    foreach ($r in $a.Access) {
        $who = try { $r.IdentityReference.Translate([System.Security.Principal.NTAccount]).Value } catch { $r.IdentityReference.Value }
        Write-Host ("    {0,-45} {1}" -f $who, $r.FileSystemRights)
    }
}

Write-Host ""
Write-Host "=== 重要提醒 ===" -ForegroundColor Cyan
Write-Host "1. 以上只能防『登录服务器后随手翻文件』，防不住管理员、快照与备份。"
Write-Host "2. 真正的兜底是轮换：密钥一旦可能外泄，就去 DeepSeek 控制台重新生成。"
Write-Host "3. 若之后改了服务运行账户，务必用 -ServiceAccount 重新授权，"
Write-Host "   否则服务读不到 .env，接口会报『尚未配置 API Key』。"
Write-Host "4. 还原方法：Import-Clixml <路径>.acl.bak | Set-Acl -Path <文件>"
Write-Host ""
