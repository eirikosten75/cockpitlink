"""Prepares the folder for the Microsoft Store package (MSIX) of CockpitLink.

    python msix/make_package.py <CockpitLink.exe> <output folder>

Copies the exe, draws the logo images from the same drawing as the program icon (cockpitlink.make_icon_image) and
writes AppxManifest.xml with the version of cockpitlink.py (3.1 -> 3.1.0.0; the Store needs the last number to be 0).
The folder is then packed with the Windows SDK's MakeAppx (see msix/build-msix.ps1).
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import cockpitlink  # noqa: E402
from PIL import Image  # noqa: E402

TARGET_SIZES = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 80, 96, 256)


def package_version(v: str) -> str:
    # CL_PACKAGE_VERSION overrides it, e.g. 3.2.1.0 for a new Store package of the same program version
    if os.environ.get("CL_PACKAGE_VERSION"):
        return os.environ["CL_PACKAGE_VERSION"]
    parts = [int(x) for x in v.split(".")][:3]
    parts += [0] * (3 - len(parts))
    return ".".join(str(p) for p in parts) + ".0"


def tile(width: int, height: int, icon_share: float) -> Image.Image:
    """The logo centred on a transparent tile, taking icon_share of the shorter side."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    size = round(min(width, height) * icon_share)
    icon = cockpitlink.make_icon_image(size)
    img.paste(icon, ((width - size) // 2, (height - size) // 2), icon)
    return img


def main() -> None:
    exe, out = Path(sys.argv[1]), Path(sys.argv[2])
    if out.exists():
        shutil.rmtree(out)
    assets = out / "Assets"
    assets.mkdir(parents=True)
    shutil.copy2(exe, out / "CockpitLink.exe")

    # App list / taskbar / Start icon, with sizes Windows picks from (plated and unplated)
    cockpitlink.make_icon_image(44).save(assets / "Square44x44Logo.png")
    for s in TARGET_SIZES:
        icon = cockpitlink.make_icon_image(s)
        icon.save(assets / f"Square44x44Logo.targetsize-{s}.png")
        icon.save(assets / f"Square44x44Logo.targetsize-{s}_altform-unplated.png")
        icon.save(assets / f"Square44x44Logo.targetsize-{s}_altform-lightunplated.png")
    cockpitlink.make_icon_image(50).save(assets / "StoreLogo.png")
    tile(71, 71, 0.70).save(assets / "SmallTile.png")
    tile(150, 150, 0.66).save(assets / "Square150x150Logo.png")
    tile(310, 150, 0.66).save(assets / "Wide310x150Logo.png")
    tile(310, 310, 0.66).save(assets / "LargeTile.png")

    manifest = (HERE / "AppxManifest.xml").read_text(encoding="utf-8")
    manifest = manifest.replace("{VERSION}", package_version(cockpitlink.VERSION))
    (out / "AppxManifest.xml").write_text(manifest, encoding="utf-8")
    print(f"Package folder ready: {out} (version {package_version(cockpitlink.VERSION)})")


if __name__ == "__main__":
    main()
