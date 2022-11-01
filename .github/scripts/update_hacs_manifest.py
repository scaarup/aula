"""Update the manifest file."""
import json
import os
import sys


def update_manifest():
    """Update the manifest file."""
    version = "0.0.0"
    manifest_path = False
    dorequirements = False

    for index, value in enumerate(sys.argv):
        if value in ["--version", "-V"]:
            version = str(sys.argv[index + 1]).replace("v", "")
        if value in ["--path", "-P"]:
            manifest_path = str(sys.argv[index + 1])[1:-1]

    if not manifest_path:
        sys.exit("Missing path to manifest file")

    with open(f"{os.getcwd()}/{manifest_path}/manifest.json",encoding="UTF-8",) as manifestfile:
        manifest = json.load(manifestfile)
    manifest["version"] = version

    with open(f"{os.getcwd()}/{manifest_path}/manifest.json","w",encoding="UTF-8",) as manifestfile:
        manifestfile.write(json.dumps(manifest, indent=4, sort_keys=True))

update_manifest()
