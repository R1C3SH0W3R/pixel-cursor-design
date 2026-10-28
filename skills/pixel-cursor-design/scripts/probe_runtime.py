"""Read-only local Python capability probe; cannot discover an agent's remote tools."""
import argparse
import json
import os
from pathlib import Path
import sys


def inspect_runtime():
    pillow = {"installed": False, "version": None, "pixel_api_compatible": False}
    try:
        from PIL import Image, __version__
        pillow.update(installed=True, version=__version__,
                      pixel_api_compatible=hasattr(Image, "Resampling") and hasattr(Image, "Dither"))
    except (ImportError, OSError) as error:
        pillow["reason"] = type(error).__name__
    python_ok = sys.version_info >= (3, 10)
    scripts = Path(__file__).resolve().parent
    helpers = {name: (scripts / name).is_file() for name in ("cursor_io.py", "package_cursors.py")}
    return {
        "scope": "This local Python process only; not the whole agent or its remote tools",
        "python": {"version": sys.version.split()[0], "compatible": python_ok},
        "pillow": pillow,
        "helper_files": helpers,
        "bundled_python_route_available": python_ok and pillow["pixel_api_compatible"] and all(helpers.values()),
        "windows_native_check_environment": os.name == "nt",
        "windows_native_check_performed": False,
        "agent_capabilities": {name: "unknown_inspect_actual_host_tools" for name in
                               ("vision", "image_generation", "alternative_execution", "file_delivery")},
        "next_step": "Map the host's actual tools before choosing a route; unavailable local Python dependencies do not imply other routes are unavailable",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    print(json.dumps(inspect_runtime(), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
