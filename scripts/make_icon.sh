#!/bin/bash

# Create an .icns file from a source PNG using macOS tools.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_IMAGE="${1:-$PROJECT_ROOT/assets/DiskViz.png}"
ICONSET_DIR="$PROJECT_ROOT/assets/DiskViz.iconset"
OUTPUT_ICNS="$PROJECT_ROOT/assets/DiskViz.icns"

if ! command -v sips >/dev/null 2>&1; then
    echo "sips 명령을 찾을 수 없습니다. macOS에서 실행해 주세요." >&2
    exit 1
fi

if ! command -v iconutil >/dev/null 2>&1; then
    echo "iconutil 명령을 찾을 수 없습니다. Xcode Command Line Tools 를 설치해 주세요." >&2
    exit 1
fi

if [ ! -f "$SRC_IMAGE" ]; then
    echo "원본 이미지가 없습니다: $SRC_IMAGE" >&2
    exit 1
fi

rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

generate_icon() {
    local size="$1"
    local filename="$2"
    sips -z "$size" "$size" "$SRC_IMAGE" --out "$ICONSET_DIR/$filename" >/dev/null
}

for base_size in 16 32 64 128 256 512; do
    generate_icon "$base_size" "icon_${base_size}x${base_size}.png"
    double_size=$((base_size * 2))
    if [ "$double_size" -le 1024 ]; then
        generate_icon "$double_size" "icon_${base_size}x${base_size}@2x.png"
    fi
done

iconutil -c icns "$ICONSET_DIR" -o "$OUTPUT_ICNS"
rm -rf "$ICONSET_DIR"

echo "아이콘 생성 완료: $OUTPUT_ICNS"
