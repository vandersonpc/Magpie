# Make an iconset folder
mkdir -p resources/Magpie.iconset

# Resize PNGs
magick resources/magpie_icon.png -resize 16x16 resources/Magpie.iconset/icon_16x16.png
magick resources/magpie_icon.png -resize 32x32 resources/Magpie.iconset/icon_16x16@2x.png
magick resources/magpie_icon.png -resize 32x32 resources/Magpie.iconset/icon_32x32.png
magick resources/magpie_icon.png -resize 64x64 resources/Magpie.iconset/icon_32x32@2x.png
magick resources/magpie_icon.png -resize 128x128 resources/Magpie.iconset/icon_128x128.png
magick resources/magpie_icon.png -resize 256x256 resources/Magpie.iconset/icon_128x128@2x.png
magick resources/magpie_icon.png -resize 256x256 resources/Magpie.iconset/icon_256x256.png
magick resources/magpie_icon.png -resize 512x512 resources/Magpie.iconset/icon_256x256@2x.png
magick resources/magpie_icon.png -resize 512x512 resources/Magpie.iconset/icon_512x512.png
magick resources/magpie_icon.png -resize 1024x1024 resources/Magpie.iconset/icon_512x512@2x.png


# Resize and create multiple PNGs
magick resources/magpie_icon.png -resize 16x16 resources/magpie-16.png
magick resources/magpie_icon.png -resize 32x32 resources/magpie-32.png
magick resources/magpie_icon.png -resize 48x48 resources/magpie-48.png
magick resources/magpie_icon.png -resize 256x256 resources/magpie-256.png

# Combine into a single .ico
icotool -c -o resources/magpie.ico resources/magpie-16.png resources/magpie-32.png resources/magpie-48.png resources/magpie-256.png


for f in resources/Magpie.iconset/*.png; do
    magick "$f" -type TrueColorAlpha "$f"
done

# magick to .icns
python resources/Magpie.iconset/create_ics.py