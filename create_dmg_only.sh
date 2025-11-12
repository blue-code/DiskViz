#!/bin/bash

# Quick DMG creation script (when app bundle already exists)

set -e

APP_NAME="DiskViz"
VERSION="1.0.0"
DMG_NAME="DiskViz-${VERSION}"
DMG_TITLE="DiskViz ${VERSION}"

echo "🔨 Creating DMG from existing app bundle..."

# Verify app bundle exists
if [ ! -d "dist/${APP_NAME}.app" ]; then
    echo "❌ Error: dist/${APP_NAME}.app not found"
    echo "Run ./build_dmg.sh first to build the app"
    exit 1
fi

# Clean up old DMG and temp
rm -rf dmg_tmp "dist/${DMG_NAME}.dmg"

# Create temp directory
mkdir -p dmg_tmp

# Copy app using ditto (macOS tool that preserves metadata)
echo "Copying app bundle..."
ditto "dist/${APP_NAME}.app" "dmg_tmp/${APP_NAME}.app"

# Create Applications symlink
ln -s /Applications dmg_tmp/Applications

# Create DMG
echo "Creating DMG..."
hdiutil create -volname "${DMG_TITLE}" \
    -srcfolder dmg_tmp \
    -ov -format UDZO \
    -imagekey zlib-level=9 \
    "dist/${DMG_NAME}.dmg"

# Cleanup
rm -rf dmg_tmp

echo "✅ DMG created: dist/${DMG_NAME}.dmg"
ls -lh "dist/${DMG_NAME}.dmg" | awk '{print "Size:", $5}'
