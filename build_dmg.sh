#!/bin/bash

# DiskViz macOS DMG Build Script
# This script builds a macOS .app bundle and creates a DMG installer

set -e  # Exit on error

echo "🔨 Building DiskViz for macOS..."
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
APP_NAME="DiskViz"
VERSION="1.0.0"
DMG_NAME="DiskViz-${VERSION}"
DMG_TITLE="DiskViz ${VERSION}"

# Step 1: Clean previous builds
echo -e "${BLUE}[1/5]${NC} Cleaning previous builds..."
rm -rf build dist *.egg-info
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
echo "✓ Cleaned"
echo ""

# Step 2: Check for py2app
echo -e "${BLUE}[2/5]${NC} Checking dependencies..."
if ! python3 -c "import py2app" 2>/dev/null; then
    echo -e "${YELLOW}Installing py2app...${NC}"
    pip3 install py2app
fi
echo "✓ Dependencies OK"
echo ""

# Step 3: Build the .app bundle
echo -e "${BLUE}[3/5]${NC} Building .app bundle..."
echo -e "${YELLOW}This may take a few minutes...${NC}"
if ! python3 setup.py py2app 2>&1 | grep -v "DeprecatedInstaller" | grep -v "fetch_build_eggs"; then
    echo -e "${RED}✗ Build failed${NC}"
    exit 1
fi
echo "✓ App bundle created"
echo ""

# Step 4: Create DMG
echo -e "${BLUE}[4/5]${NC} Creating DMG installer..."

# Create temporary directory for DMG contents
DMG_TMP="dmg_tmp"
mkdir -p "${DMG_TMP}"

# Copy app to temp directory
cp -r "dist/${APP_NAME}.app" "${DMG_TMP}/"

# Create Applications symlink
ln -s /Applications "${DMG_TMP}/Applications"

# Create DMG
hdiutil create -volname "${DMG_TITLE}" \
    -srcfolder "${DMG_TMP}" \
    -ov -format UDZO \
    "dist/${DMG_NAME}.dmg"

# Clean up temp directory
rm -rf "${DMG_TMP}"

echo "✓ DMG created"
echo ""

# Step 5: Show results
echo -e "${BLUE}[5/5]${NC} Build complete!"
echo ""
echo -e "${GREEN}✨ Successfully built DiskViz!${NC}"
echo ""
echo "Output files:"
echo "  📦 dist/${APP_NAME}.app"
echo "  💿 dist/${DMG_NAME}.dmg"
echo ""
echo "DMG size: $(du -h "dist/${DMG_NAME}.dmg" | cut -f1)"
echo ""
echo "To install:"
echo "  1. Open dist/${DMG_NAME}.dmg"
echo "  2. Drag ${APP_NAME}.app to Applications folder"
echo ""
