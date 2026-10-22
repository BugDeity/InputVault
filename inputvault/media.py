"""图片编码和缩略图。只用 QImage / QImageReader，可以在后台线程里调用。"""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader

THUMB_SIZE = 160


def encode_png(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def thumbnail_png(image: QImage) -> bytes:
    if image.width() > THUMB_SIZE or image.height() > THUMB_SIZE:
        image = image.scaled(
            THUMB_SIZE, THUMB_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    return encode_png(image)


def thumbnail_from_file(path: str) -> bytes | None:
    """按缩略图尺寸直接解码，大照片也不用整张读进内存。读不了的格式返回 None。"""
    reader = QImageReader(path)
    reader.setAutoTransform(True)  # 照片的 EXIF 方向
    size = reader.size()
    if size.isValid() and (size.width() > THUMB_SIZE or size.height() > THUMB_SIZE):
        reader.setScaledSize(size.scaled(THUMB_SIZE, THUMB_SIZE, Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    return None if image.isNull() else encode_png(image)
