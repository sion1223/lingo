"""Audit unmodified official TTF/OFL bundles; requires fontTools for development only."""
import hashlib
import json
from pathlib import Path
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parents[1] / "web/fonts"
SOURCES = [
    ("klee-one", "Klee One", "KleeOne-Regular.ttf", "fontworks-fonts/Klee", "8b0532731b63ad8a445ca341d8d7d941079b83ab"),
    ("zen-kurenaido", "Zen Kurenaido", "ZenKurenaido-Regular.ttf", "googlefonts/zen-kurenaido", "cd751ced55d4ebb1fb2fbd90602eb9351dd03fd6"),
    ("yomogi", "Yomogi", "Yomogi-Regular.ttf", "satsuyako/YomogiFont", "8551d68706679b88ce6ff40e093a993e546e593e"),
]


def audit():
    output = []
    for slug, family, filename, repository, revision in SOURCES:
        path = ROOT / slug / filename
        license_path = ROOT / slug / "OFL.txt"
        license_text = license_path.read_text(encoding="utf-8-sig")
        if "SIL OPEN FONT LICENSE Version 1.1" not in license_text:
            raise ValueError(f"Missing OFL 1.1: {slug}")
        with TTFont(path) as font:
            ranges = []
            for cp in sorted(font.getBestCmap()):
                if ranges and cp == ranges[-1][1] + 1:
                    ranges[-1][1] = cp
                else:
                    ranges.append([cp, cp])
        output.append({
            "id": slug, "family": family, "file": f"{slug}/{filename}",
            "license": f"{slug}/OFL.txt", "license_id": "OFL-1.1", "modified": False,
            "source_url": f"https://github.com/{repository}/tree/{revision}",
            "download_url": f"https://raw.githubusercontent.com/{repository}/{revision}/fonts/ttf/{filename}",
            "revision": revision, "copyright": license_text.splitlines()[0],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "license_sha256": hashlib.sha256(license_path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size, "coverage": ranges,
        })
    return {"schema_version": 1, "fonts": output}


if __name__ == "__main__":
    (ROOT / "manifest.json").write_text(json.dumps(audit(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Audited 3 official font bundles and Unicode coverage.")
