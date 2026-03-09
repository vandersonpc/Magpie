

#pyinstaller magpie.spec --clean --distpath dist


wget -q https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage
#wget -q https://github.com/linuxdeploy/linuxdeploy-plugin-qt/releases/download/continuous/linuxdeploy-plugin-qt-x86_64.AppImage

chmod +x linuxdeploy-x86_64.AppImage
#chmod +x linuxdeploy-plugin-qt-x86_64.AppImage

export LINUXDEPLOY_PLUGIN_PATH=$PWD

mkdir -p AppDir/usr/bin
mkdir -p AppDir/usr/share/icons/hicolor/256x256/apps

cp -r dist/Magpie/* AppDir/usr/bin/

cp resources/magpie-256.png AppDir/usr/share/icons/hicolor/256x256/apps/magpie.png

cat > AppDir/magpie.desktop << EOF
[Desktop Entry]
Name=Magpie
Exec=Magpie
Icon=magpie
Type=Application
Categories=Utility;
Terminal=false
StartupWMClass=Magpie
EOF

#export QMAKE=/usr/lib/x86_64-linux-gnu/qt6/bin/qmake

./linuxdeploy-x86_64.AppImage \
# --appdir AppDir \
# --desktop-file AppDir/magpie.desktop \
# --icon-file AppDir/usr/share/icons/hicolor/256x256/apps/magpie.png \
# --executable AppDir/usr/bin/Magpie/Magpie \
# --output appimage

#Workin

cp -r dist/Magpie/_internal img/usr/bin/

./linuxdeploy-x86_64.AppImage \
--appdir AppDir \
--desktop-file AppDir/magpie.desktop   \
--icon-file AppDir/usr/share/icons/hicolor/256x256/apps/magpie.png   \
--executable AppDir/usr/bin/Magpie  \
--output appimage 

