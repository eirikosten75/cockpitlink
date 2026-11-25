# Builds the Microsoft Store package CockpitLink-<version>.msix from CockpitLink.exe (build it first with build.ps1).
# Needs the Windows SDK (MakeAppx.exe). GitHub Actions runs this in .github/workflows/build.yml.
# The package is not signed: the Microsoft Store signs it when it is submitted in Partner Center.
param([string]$Exe = (Join-Path (Split-Path -Parent $PSScriptRoot) "CockpitLink.exe"))

$ErrorActionPreference = "Stop"
$root  = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $env:TEMP "cockpitlink-msix"

python (Join-Path $PSScriptRoot "make_package.py") $Exe $stage
if ($LASTEXITCODE -ne 0) { throw "make_package.py failed" }

$makeappx = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\makeappx.exe" -ErrorAction SilentlyContinue |
    Sort-Object FullName -Descending | Select-Object -First 1
if (-not $makeappx) { throw "MakeAppx.exe not found – install the Windows SDK" }

$version = ([xml](Get-Content (Join-Path $stage "AppxManifest.xml"))).Package.Identity.Version
$out = Join-Path $root "CockpitLink-$version.msix"
& $makeappx.FullName pack /o /d $stage /p $out
if ($LASTEXITCODE -ne 0) { throw "MakeAppx failed" }
Write-Host "Done: $out"
