# Builds VirpilLedBridge.exe from virpil_bridge.py and ui.html (plus layouts.json as the built-in default).
# Only needed when virpil_bridge.py or ui.html has changed. Changes to config.json and layouts.json
# work without a rebuild.
# Requires: python -m pip install -r requirements-build.txt
# Releases are built the same way by GitHub Actions (.github/workflows/build.yml).
#
# The exe has no console window. It is controlled from the tray icon and the control panel
# at http://127.0.0.1:8765/. The log is also written to bridge.log next to config.json.

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$work = Join-Path $env:TEMP "virpil-led-bridge-build"
$ico  = Join-Path $here "icon.ico"

# Make the program icon from the same drawing as the tray icon.
python -c "import sys; sys.path.insert(0, r'$here'); import virpil_bridge as v; v.make_icon_image().save(r'$ico', sizes=[(16,16),(32,32),(48,48),(64,64)])"

python -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name VirpilLedBridge `
    --icon $ico `
    --collect-all SimConnect `
    --hidden-import pystray._win32 `
    --add-data "$(Join-Path $here 'ui.html');." `
    --add-data "$(Join-Path $here 'layouts.json');." `
    --distpath $here `
    --workpath (Join-Path $work "work") `
    --specpath $work `
    (Join-Path $here "virpil_bridge.py")

if ($LASTEXITCODE -eq 0) {
    Write-Host "Done: $(Join-Path $here 'VirpilLedBridge.exe')"
}
