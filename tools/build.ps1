[CmdletBinding()]
param(
    [ValidateSet('x64', 'x86')]
    [string]$Platform = 'x64',
    [ValidateSet('Release', 'Debug')]
    [string]$Configuration = 'Release',
    [string]$SigningThumbprint = $env:CODEX_TRAFFICMONITOR_SIGN_THUMBPRINT,
    [switch]$SkipSign
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root "build\$Platform\$Configuration"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$vcvarsCandidates = @(
    "${env:ProgramFiles}\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars64.bat",
    "${env:ProgramFiles}\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvars32.bat",
    "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
    "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
)

$vcvars = $null
if ($Platform -eq 'x64') {
    $vcvars = $vcvarsCandidates | Where-Object { $_ -like '*vcvars64.bat' -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}
else {
    $vcvars = $vcvarsCandidates | Where-Object { $_ -like '*vcvars32.bat' -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
}

if (-not $vcvars) {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $vswhere) {
        $installPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if ($installPath) {
            $candidate = Join-Path $installPath "VC\Auxiliary\Build\$(if ($Platform -eq 'x64') { 'vcvars64.bat' } else { 'vcvars32.bat' })"
            if (Test-Path -LiteralPath $candidate) {
                $vcvars = $candidate
            }
        }
    }
}

if (-not $vcvars) {
    throw "找不到 Visual Studio C++ 构建环境。请安装 VS Build Tools，或用已配置的 Developer PowerShell 手动运行 cl。"
}

$optimization = if ($Configuration -eq 'Release') { '/O2 /DNDEBUG' } else { '/Od /Zi /D_DEBUG' }
$source = Join-Path $root 'src\CodexUsagePlugin.cpp'
$include = Join-Path $root 'include'
$dll = Join-Path $outDir 'CodexUsage.dll'
$pdb = Join-Path $outDir 'CodexUsage.pdb'
$obj = Join-Path $outDir 'CodexUsagePlugin.obj'

$compile = @(
    'cl',
    '/nologo',
    '/std:c++17',
    '/EHsc',
    '/LD',
    '/utf-8',
    '/DUNICODE',
    '/D_UNICODE',
    '/DWIN32_LEAN_AND_MEAN',
    '/DNOMINMAX',
    $optimization,
    "/I`"$include`"",
    "/Fo`"$obj`"",
    "`"$source`"",
    "/Fe:`"$dll`"",
    '/link',
    '/NOLOGO',
    '/DLL',
    "Shell32.lib",
    "User32.lib",
    "Gdi32.lib",
    "/PDB:`"$pdb`""
) -join ' '

$cmd = "call `"$vcvars`" && $compile"
cmd.exe /d /s /c $cmd
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

if (-not $SkipSign) {
    if (-not $SigningThumbprint) {
        $cert = Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
            Where-Object { $_.NotAfter -gt (Get-Date) } |
            Sort-Object NotAfter -Descending |
            Select-Object -First 1
        if ($cert) {
            $SigningThumbprint = $cert.Thumbprint
        }
    }

    if ($SigningThumbprint) {
        $signtool = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Recurse -Filter signtool.exe -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match "\\$Platform\\signtool\.exe$" } |
            Sort-Object FullName -Descending |
            Select-Object -First 1
        if ($signtool) {
            & $signtool.FullName sign /fd SHA256 /sha1 $SigningThumbprint $dll
            if ($LASTEXITCODE -ne 0) {
                exit $LASTEXITCODE
            }
        }
        else {
            Write-Warning "signtool.exe not found; built DLL is unsigned."
        }
    }
    else {
        Write-Warning "No code-signing certificate found; built DLL is unsigned."
    }
}

$scriptDest = Join-Path $outDir 'scripts'
$resolvedOut = [System.IO.Path]::GetFullPath($outDir)
$resolvedScriptDest = [System.IO.Path]::GetFullPath($scriptDest)
if (-not $resolvedScriptDest.StartsWith($resolvedOut, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to clean unexpected script output path: $resolvedScriptDest"
}
if (Test-Path -LiteralPath $scriptDest) {
    Remove-Item -LiteralPath $scriptDest -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $scriptDest | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'scripts\collect_codex_usage.py') -Destination $scriptDest -Force
Copy-Item -LiteralPath (Join-Path $root 'scripts\update_codex_usage.ps1') -Destination $scriptDest -Force
Write-Host "Built $dll"
