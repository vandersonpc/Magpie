import icnsutil
import os

# Get the folder where the script is located
script_dir = os.path.dirname(os.path.abspath(__file__))

# Build full paths to each PNG
icon_files = [
    "icon_16x16.png",
    "icon_16x16@2x.png",
    "icon_32x32.png",
    "icon_32x32@2x.png",
    "icon_128x128.png",
    "icon_128x128@2x.png",
    "icon_256x256.png",
    "icon_256x256@2x.png",
    "icon_512x512.png",
    "icon_512x512@2x.png"
]

icon = icnsutil.IcnsFile()

for f in icon_files:
    path = os.path.join(script_dir, f)
    icon.add_media(file=path)

icon.write(os.path.join(script_dir, "resources/magpie.icns"))