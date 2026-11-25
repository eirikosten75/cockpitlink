# Builds CockpitLink.exe from cockpitlink.py and ui.html (plus the layouts library folder and config.example.json).
# Only needed when cockpitlink.py or ui.html has changed. Changes to config.json and layouts.json
# work without a rebuild.
# Requires: python -m pip install -r requirements-build.txt
# Releases are built the same way by GitHub Actions (.github/workflows/build.yml).
#
# The exe has no console window. It is controlled from the tray icon and the control panel
# at http://127.0.0.1:8765/. The log is also written to bridge.log next to config.json.

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$work = Join-Path $env:TEMP "cockpitlink-build"
$ico  = Join-Path $here "icon.ico"

# Make the program icon from the same drawing as the tray icon.
python -c "import sys; sys.path.insert(0, r'$here'); import cockpitlink as v; v.make_icon_image(256).save(r'$ico', sizes=[(16,16),(20,20),(24,24),(32,32),(40,40),(48,48),(64,64),(128,128),(256,256)])"

python -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name CockpitLink `
    --icon $ico `
    --collect-all SimConnect `
    --hidden-import pystray._win32 `
    --add-data "$(Join-Path $here 'ui.html');." `
    --add-data "$(Join-Path $here 'layouts');layouts" `
    --add-data "$(Join-Path $here 'config.example.json');." `
    --distpath $here `
    --workpath (Join-Path $work "work") `
    --specpath $work `
    (Join-Path $here "cockpitlink.py")

if ($LASTEXITCODE -eq 0) {
    Write-Host "Done: $(Join-Path $here 'CockpitLink.exe')"
}
