#requires -version 5.1
<#
Spotify Overlay Lyrics Windows launcher v0.0.2.
Windows PowerShell 5.1 treats redirected native stderr as errors when
$ErrorActionPreference='Stop'. Run Python/pip under a scoped Continue policy,
retain ALL stdout/stderr in setup.log, and inspect the real process exit code.
#>
[CmdletBinding()]
param(
    [ValidateSet('Run', 'Demo', 'Install', 'Diagnose', 'Debug', 'Build')]
    [string]$Mode = 'Run'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$localData = Join-Path $env:LOCALAPPDATA 'SpotifyOverlayLyrics'
# v1.0.27 keeps the venv in user-writable AppData. Any partial v1.0.2
# project-root .venv is ignored, without deleting user files.
$venvDir = Join-Path $localData 'runtime-venv'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'
$setupLog = Join-Path $localData 'setup.log'
$installerLog = Join-Path $localData 'python-installer.log'
$applicationLog = Join-Path $localData 'application.log'
$pythonVersion = '3.13.16'
$pythonInstallerUrl = 'https://www.python.org/ftp/python/3.13.16/python-3.13.16-amd64.exe'
$pythonInstallerSha256 = 'fb4f9f5d438b2396da0086dc70b935c530cb578e37adc6d354f7ad2037fee83b'
$managedPythonDir = Join-Path $env:LOCALAPPDATA 'Programs\SpotifyOverlayLyrics\Python313'
$managedPython = Join-Path $managedPythonDir 'python.exe'

function Write-SetupLog([string]$Message) {
    try {
        if (-not (Test-Path -LiteralPath $localData)) {
            New-Item -ItemType Directory -Force -Path $localData | Out-Null
        }
        ('{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) |
            Out-File -LiteralPath $setupLog -Encoding utf8 -Append
    } catch {
        # Logging must not prevent startup.
    }
}

function Write-Stage([string]$Message) {
    Write-Host ('[Spotify Lyrics] ' + $Message) -ForegroundColor Cyan
    Write-SetupLog $Message
}

# The primary regression fix: never allow Windows PowerShell 5.1 to turn
# Python's first stderr line into a terminating NativeCommandError.
# Log full native output even on failure, and check $LASTEXITCODE explicitly.
function Invoke-Native {
    param(
        [Parameter(Mandatory=$true)][string]$Command,
        [Parameter(Mandatory=$true)][string[]]$Arguments,
        [Parameter(Mandatory=$true)][string]$Stage
    )
    Write-SetupLog ('Command stage: ' + $Stage + '; executable: ' + $Command)
    $originalPreference = $ErrorActionPreference
    $exitCode = -1
    try {
        $ErrorActionPreference = 'Continue'
        & $Command @Arguments 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                $line = $_.Exception.Message
            } else {
                $line = [string]$_
            }
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                Write-Host $line
                Write-SetupLog ('[' + $Stage + '] ' + $line)
            }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $originalPreference
    }
    Write-SetupLog ('Command stage exit: ' + $Stage + ' => ' + $exitCode)
    if ($exitCode -ne 0) {
        throw ('執行「' + $Stage + '」失敗（代碼 ' + $exitCode + '）。完整錯誤已保存在 ' + $setupLog)
    }
}

function Test-Native {
    param(
        [Parameter(Mandatory=$true)][string]$Command,
        [string[]]$Arguments = @()
    )
    $originalPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & $Command @Arguments *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $originalPreference
    }
}

function Test-CompatiblePython([string]$Command, [string[]]$Prefix = @()) {
    $probe = "import sys, struct; v=sys.version_info[:2]; sys.exit(0 if (3,11) <= v <= (3,13) and struct.calcsize('P') == 8 else 1)"
    return (Test-Native -Command $Command -Arguments (@($Prefix) + @('-c', $probe)))
}

function Find-CompatiblePython {
    $candidates = New-Object 'System.Collections.Generic.List[object]'

    # Prefer the SHA-256-verified, privately managed interpreter. The py.exe
    # launcher may otherwise resolve a Store alias or another installation.
    foreach ($location in @(
        $managedPython,
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        (Join-Path $env:ProgramFiles 'Python313\python.exe'),
        (Join-Path $env:ProgramFiles 'Python312\python.exe'),
        (Join-Path $env:ProgramFiles 'Python311\python.exe')
    )) {
        if (Test-Path -LiteralPath $location -PathType Leaf) {
            $candidates.Add([pscustomobject]@{ Command = $location; Prefix = @(); Label = $location })
        }
    }
    if (Get-Command 'py.exe' -ErrorAction SilentlyContinue) {
        foreach ($version in @('3.13', '3.12', '3.11')) {
            $candidates.Add([pscustomobject]@{ Command = 'py.exe'; Prefix = @('-' + $version); Label = ('py -' + $version) })
        }
    }
    foreach ($name in @('python.exe', 'python3.exe')) {
        $found = Get-Command $name -ErrorAction SilentlyContinue
        if ($found -and $found.Source -notmatch '[\\/]WindowsApps[\\/]') {
            $candidates.Add([pscustomobject]@{ Command = $found.Source; Prefix = @(); Label = $found.Source })
        }
    }

    foreach ($candidate in $candidates) {
        if (Test-CompatiblePython -Command $candidate.Command -Prefix $candidate.Prefix) {
            Write-Stage ('找到可用 Python：' + $candidate.Label)
            return $candidate
        }
    }
    return $null
}

function Install-ManagedPython {
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw '本工具需要 64 位元 Windows 10/11。'
    }

    Write-Stage ('未找到 64 位元 Python 3.11～3.13；自動安裝 Python ' + $pythonVersion + '。')
    Write-Stage '安裝位置為目前使用者資料夾，不會覆蓋既有 Python。'
    Write-Stage ('官方來源：' + $pythonInstallerUrl)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $installerPath = Join-Path $env:TEMP ('SpotifyOverlayLyrics-python-' + $pythonVersion + '-amd64.exe')
    try {
        Write-Stage '下載 Python 官方安裝程式（約 29 MB）...'
        Invoke-WebRequest -UseBasicParsing -Uri $pythonInstallerUrl -OutFile $installerPath -TimeoutSec 300
        $actualHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $pythonInstallerSha256) {
            throw ('安裝檔 SHA-256 驗證失敗；不會執行此檔案。實際值：' + $actualHash)
        }
        Write-Stage '官方 SHA-256 已驗證。正在安裝至使用者資料夾...'
        $argList = @(
            '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'AppendPath=0',
            'Include_pip=1', 'Include_launcher=0', 'Include_test=0',
            ('TargetDir="' + $managedPythonDir + '"'),
            '/log', ('"' + $installerLog + '"')
        )
        Write-SetupLog ('Starting verified Python installer: ' + $installerPath)
        $process = Start-Process -FilePath $installerPath -ArgumentList $argList -Wait -PassThru
        Write-SetupLog ('Python installer exit code: ' + $process.ExitCode)
        if ($process.ExitCode -notin @(0, 3010)) {
            throw ('Python 自動安裝失敗（代碼 ' + $process.ExitCode + '）。安裝紀錄：' + $installerLog)
        }
    } finally {
        Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-CompatiblePython -Command $managedPython)) {
        throw ('Python 安裝完成，但在預期目錄找不到 64 位元 Python：' + $managedPython)
    }
    Write-Stage 'Python 安裝完成。'
}

function Find-OrInstallPython {
    $candidate = Find-CompatiblePython
    if ($null -eq $candidate) {
        Install-ManagedPython
        $candidate = Find-CompatiblePython
    }
    if ($null -eq $candidate) {
        throw '仍找不到相容 Python。請執行「診斷環境.bat」並提供結果。'
    }
    return $candidate
}

function Test-VenvPip {
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) { return $false }
    return (Test-Native -Command $venvPython -Arguments @('-m', 'pip', '--version'))
}

function Ensure-Pip {
    if (Test-VenvPip) { return }
    Write-Stage '虛擬環境尚未安裝 pip，正在自動修復...'
    $candidate = Find-OrInstallPython
    # Preferred: use the base interpreter's pip to install pip into a venv
    # created with --without-pip. pip >=22.3 supports --python <venv>.
    $pipProbeArgs = @($candidate.Prefix) + @('-m', 'pip', '--version')
    if (Test-Native -Command $candidate.Command -Arguments $pipProbeArgs) {
        try {
            Write-Stage '從 Python 原生 pip 修復虛擬環境...'
            $bootstrapArgs = @($candidate.Prefix) + @('-m', 'pip', '--python', $venvDir,
                'install', '--disable-pip-version-check', 'pip')
            Invoke-Native -Command $candidate.Command -Arguments $bootstrapArgs -Stage 'pip bootstrap (base pip --python)'
        } catch {
            Write-SetupLog ('Base pip repair failed: ' + $_.Exception.Message)
            Write-Stage '原生 pip 修復未完成，改用 ensurepip 備援。'
        }
    }
    if (-not (Test-VenvPip)) {
        Invoke-Native -Command $venvPython -Arguments @('-m','ensurepip','--upgrade') -Stage 'pip bootstrap (ensurepip)'
    }
    if (-not (Test-VenvPip)) {
        throw ('虛擬環境 pip 修復失敗。請提供 ' + $setupLog)
    }
}

function Ensure-Environment {
    if (Test-Path -LiteralPath $venvDir) {
        if (-not (Test-Path -LiteralPath (Join-Path $venvDir 'pyvenv.cfg'))) {
            throw ('此目錄已存在，但不是有效的虛擬環境：' + $venvDir + '。為避免誤刪資料，請提供 setup.log。')
        }
        if (-not (Test-CompatiblePython -Command $venvPython)) {
            Write-Stage '發現不完整或損壞的虛擬環境，正在重新建立。'
            Remove-Item -LiteralPath $venvDir -Recurse -Force
        }
    }

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        Write-Stage '檢查可用 Python...'
        $candidate = Find-OrInstallPython
        Write-Stage ('建立專用虛擬環境：' + $venvDir)
        Write-Stage '使用 --without-pip 避開 Windows ensurepip 建立階段錯誤。'
        # Do not run ensurepip during venv creation. Bootstrapping pip happens
        # separately with complete stderr capture, so partial creation can heal.
        $createArgs = @($candidate.Prefix) + @('-m', 'venv', '--without-pip', $venvDir)
        Invoke-Native -Command $candidate.Command -Arguments $createArgs -Stage 'create venv --without-pip'
        if (-not (Test-CompatiblePython -Command $venvPython)) {
            throw ('建立虛擬環境後 Python 無法執行：' + $venvPython)
        }
    }

    Ensure-Pip
    $imports = 'from PySide6.QtWidgets import QApplication; import requests; import winrt.windows.media.control; import winrt.windows.storage.streams; import unidecode; from opencc import OpenCC; import comtypes; from pycaw.pycaw import AudioUtilities, IAudioMeterInformation, IAudioSessionControl2'
    if (-not (Test-Native -Command $venvPython -Arguments @('-c', $imports))) {
        Write-Stage '安裝或修復必要套件...'
        Invoke-Native -Command $venvPython -Arguments @('-m', 'pip', 'install',
            '--disable-pip-version-check', '-r', (Join-Path $projectRoot 'requirements.txt')) -Stage 'install dependencies'
        if (-not (Test-Native -Command $venvPython -Arguments @('-c', $imports))) {
            Write-Stage '套件安裝已完成，但仍無法匯入；以下將印出完整 Python 錯誤。'
            Invoke-Native -Command $venvPython -Arguments @('-c', $imports) -Stage 'verify imports'
            throw '依賴匯入失敗，請提供 setup.log。'
        }
    }

    Write-Stage 'Python 與套件檢查通過。'
    return $venvPython
}

# Ensure the compiled OBS frontend is present BEFORE freezing the EXE.
# Shipping only the Vue source does not work: Node is not required at runtime.
function Assert-FrontendReady {
    $webRoot = Join-Path $projectRoot 'frontend\dist'
    $indexPath = Join-Path $webRoot 'index.html'
    if (-not (Test-Path -LiteralPath $indexPath -PathType Leaf)) {
        throw '缺少 OBS 編譯資源 frontend\dist\index.html；請重新解壓縮完整 ZIP。原始碼開發者可執行 cd frontend; npm run build。'
    }
    $html = Get-Content -LiteralPath $indexPath -Raw -Encoding UTF8
    if ($html -match '__OVERLAY_CSS__|__VUE_JS__|__OVERLAY_JS__') {
        throw 'OBS 前端尚未完成編譯，無法打包 EXE。'
    }
    $matches = [regex]::Matches($html, '(?:src|href)=["''](/assets/[A-Za-z0-9._-]+\.(?:js|css))["'']')
    if ($matches.Count -lt 3) {
        throw 'OBS 前端不完整：找不到 Vue、CSS 與應用程式腳本。'
    }
    foreach ($match in $matches) {
        $filename = [IO.Path]::GetFileName($match.Groups[1].Value)
        $asset = Join-Path (Join-Path $webRoot 'assets') $filename
        if (-not (Test-Path -LiteralPath $asset -PathType Leaf)) {
            throw ('OBS 前端缺少資源：' + $filename)
        }
    }
    Write-Stage 'OBS 離線資源檢查通過。'
}

function Show-Diagnostics {
    Write-Host '=== Spotify Overlay Lyrics v0.0.2 - 診斷 ==='
    Write-Host ('Windows: ' + [Environment]::OSVersion.VersionString)
    Write-Host ('64-bit OS: ' + [Environment]::Is64BitOperatingSystem)
    Write-Host ('PowerShell: ' + $PSVersionTable.PSVersion.ToString())
    Write-Host ('Project folder: ' + $projectRoot)
    Write-Host ('Runtime venv: ' + $venvDir)
    Write-Host ''
    Write-Host 'Installed Python launcher:'
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $originalPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = 'Continue'
            & py.exe -0p 2>&1 | Out-Host
        } finally {
            $ErrorActionPreference = $originalPreference
        }
    } else { Write-Host 'py.exe not found' }
    Write-Host 'PATH Python:'
    $originalPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'SilentlyContinue'
        & where.exe python 2>&1 | Out-Host
    } finally {
        $ErrorActionPreference = $originalPreference
    }
    Write-Host ''
    if (Test-CompatiblePython -Command $venvPython) {
        Write-Host ('Virtual environment: ' + $venvPython)
        try { Invoke-Native -Command $venvPython -Arguments @('-V') -Stage 'diagnose python version' } catch { Write-Host $_.Exception.Message }
        try { Invoke-Native -Command $venvPython -Arguments @('-m','pip','list') -Stage 'diagnose installed packages' } catch { Write-Host $_.Exception.Message }
    } else { Write-Host 'Runtime venv not created or invalid' }
    $legacyVenv = Join-Path $projectRoot '.venv'
    if (Test-Path -LiteralPath $legacyVenv) {
        Write-Host ('Legacy v1.0.2 .venv (not used): ' + $legacyVenv)
    }
    foreach ($log in @($setupLog, $installerLog, $applicationLog)) {
        Write-Host ''
        Write-Host ('--- ' + $log + ' ---')
        if (Test-Path -LiteralPath $log) {
            Get-Content -LiteralPath $log -Encoding UTF8 -Tail 70 | Out-Host
        } else { Write-Host 'No log yet.' }
    }
    Write-Host ''
    Write-Host '如果啟動失敗，請提供上方完整錯誤或 setup.log 最後 70 行。'
}

try {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'main.py'))) {
        throw '找不到 main.py：請先將 ZIP 全部解壓縮，不要在 ZIP 預覽畫面直接雙擊 BAT。'
    }
    Write-Host ('Spotify Overlay Lyrics v0.0.2 | Mode: ' + $Mode)
    Write-Host ('Project: ' + $projectRoot)
    Write-Host ''
    if ($Mode -eq 'Diagnose') {
        Show-Diagnostics
        exit 0
    }
    $python = Ensure-Environment
    if ($Mode -eq 'Install') {
        Write-Host '安裝完成。可以啟動 Spotify 浮動歌詞了。' -ForegroundColor Green
        exit 0
    }
    if ($Mode -eq 'Build') {
        Assert-FrontendReady
        Write-Stage '安裝 PyInstaller...'
        Invoke-Native -Command $python -Arguments @('-m','pip','install',
            '--disable-pip-version-check','pyinstaller>=6.13,<7') -Stage 'install PyInstaller'
        $buildArgs = @(
            '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--windowed',
            '--name', 'SpotifyOverlayLyrics', '--icon', 'assets\app.ico',
            '--collect-submodules', 'winrt',
            '--collect-submodules', 'pycaw',
            '--collect-data', 'opencc',
            '--add-data', 'frontend\dist;frontend\dist',
            '--add-data', 'assets\spotify_mark.png;assets',
            '--add-data', 'assets\spotify_mark.svg;assets', 'main.py'
        )
        Invoke-Native -Command $python -Arguments $buildArgs -Stage 'build Windows EXE'
        Write-Host '打包完成：dist\SpotifyOverlayLyrics.exe' -ForegroundColor Green
        exit 0
    }

    Write-Stage '啟動 Windows 浮動歌詞視窗...'
    Write-Host '黑色視窗是診斷控制台，請先最小化，不要關閉。'
    if ($Mode -eq 'Demo') {
        Invoke-Native -Command $python -Arguments @((Join-Path $projectRoot 'main.py'), '--demo') -Stage 'run demo UI'
    } else {
        Invoke-Native -Command $python -Arguments @((Join-Path $projectRoot 'main.py')) -Stage 'run Spotify UI'
    }
    Write-Host '程式已結束。'
    exit 0
} catch {
    Write-Host ''
    Write-Host ('[啟動失敗] ' + $_.Exception.Message) -ForegroundColor Red
    Write-SetupLog ('ERROR: ' + ($_ | Out-String))
    if ($_.ScriptStackTrace) {
        Write-SetupLog ('PowerShell callstack: ' + $_.ScriptStackTrace)
    }
    Write-Host ('環境日誌：' + $setupLog)
    Write-Host ('應用程式日誌：' + $applicationLog)
    Write-Host ('安裝程式日誌：' + $installerLog)
    Write-Host '請截圖完整錯誤，或執行「診斷環境.bat」。'
    exit 1
}
