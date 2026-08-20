from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def align_image(
    source_path: Path,
    destination_path: Path,
    rotation: int,
    corners: list[list[float]] | None,
) -> tuple[int, int]:
    image = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("无法读取上传的图片。")

    if rotation == 90:
        image = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    elif rotation == 180:
        image = cv2.rotate(image, cv2.ROTATE_180)
    elif rotation == 270:
        image = cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)

    if corners:
        height, width = image.shape[:2]
        source = np.float32([[x * width, y * height] for x, y in corners])
        target_height = 2200
        target_width = round(target_height * 595.32 / 842.04)
        target = np.float32(
            [
                [0, 0],
                [target_width - 1, 0],
                [target_width - 1, target_height - 1],
                [0, target_height - 1],
            ]
        )
        matrix = cv2.getPerspectiveTransform(source, target)
        image = cv2.warpPerspective(
            image,
            matrix,
            (target_width, target_height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(destination_path), image, [cv2.IMWRITE_JPEG_QUALITY, 94]):
        raise ValueError("无法保存校正后的图片。")
    height, width = image.shape[:2]
    return width, height
