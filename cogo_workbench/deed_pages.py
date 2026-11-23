"""Deed files as page images for a vision model.

Every page is rendered. A PDF text layer is not used: embedded text is a poor
reading of bearings and fractions. Qt reads images, including multi-page TIFF.
PDFs use Qt's PDF module, then GDAL, then Poppler's pdftoppm if it is installed.
"""
import base64
import importlib
import os
import shutil
import subprocess  # nosec B404 - only pdftoppm, found on PATH, fixed arguments
import tempfile

from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QSize, Qt, QT_VERSION
from qgis.PyQt.QtGui import QImage, QImageReader

MAX_SIDE = 1800


class DeedReadError(RuntimeError):
    pass


def _to_b64(img):
    if img.isNull():
        raise DeedReadError("A document page could not be rendered.")
    if max(img.width(), img.height()) > MAX_SIDE:
        img = img.scaled(MAX_SIDE, MAX_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)
    raw = QByteArray()
    buf = QBuffer(raw)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    saved = img.save(buf, "PNG")
    buf.close()
    if not saved:
        raise DeedReadError("A document page could not be encoded as PNG.")
    return base64.b64encode(bytes(raw)).decode("ascii")


def image_pages(path):
    reader = QImageReader(path)
    pages = []
    for index in range(max(1, reader.imageCount())):
        if index:
            reader = QImageReader(path)
            if not reader.jumpToImage(index):
                raise DeedReadError("Couldn't seek to image page %d." % (index + 1))
        img = reader.read()
        if img.isNull():
            raise DeedReadError("Couldn't read image page %d (%s)." % (index + 1, reader.errorString()))
        pages.append(_to_b64(img))
    return pages


def _pdf_document():
    try:
        from qgis.PyQt.QtPdf import QPdfDocument
    except ImportError:
        try:
            QPdfDocument = importlib.import_module("PyQt%d.QtPdf" % (QT_VERSION >> 16)).QPdfDocument
        except ImportError:
            return None
    return QPdfDocument(None)


def _pdf_qt(path, dpi):
    doc = _pdf_document()
    if doc is None:
        return None
    doc.load(path)
    if doc.pageCount() <= 0:
        return None
    pages = []
    for index in range(doc.pageCount()):
        pts = doc.pagePointSize(index)
        size = QSize(int(pts.width() / 72.0 * dpi), int(pts.height() / 72.0 * dpi))
        pages.append(_to_b64(doc.render(index, size)))
    return pages


def _pdf_gdal(path, dpi):
    try:
        from osgeo import gdal
    except ImportError:
        return None
    gdal.SetConfigOption("GDAL_PDF_DPI", str(dpi))
    dataset = gdal.Open(path)
    if dataset is None:
        return None
    sources = [item[0] for item in (dataset.GetSubDatasets() or [])] or [path]
    pages = []
    for src in sources:
        mem = "/vsimem/cogo_deed_page.png"
        out = gdal.Translate(mem, src, format="PNG")
        if out is None:
            continue
        out = None
        handle = gdal.VSIFOpenL(mem, "rb")
        gdal.VSIFSeekL(handle, 0, 2)
        size = gdal.VSIFTellL(handle)
        gdal.VSIFSeekL(handle, 0, 0)
        data = gdal.VSIFReadL(1, size, handle)
        gdal.VSIFCloseL(handle)
        gdal.Unlink(mem)
        img = QImage.fromData(data)
        if not img.isNull():
            pages.append(_to_b64(img))
    return pages or None


def _pdf_poppler(path, dpi):
    exe = shutil.which("pdftoppm")
    if not exe:
        return None
    tmp = tempfile.mkdtemp(prefix="cogo_deed_")
    try:
        subprocess.run([exe, "-r", str(int(dpi)), "-png", path, os.path.join(tmp, "p")],  # nosec B603
                       check=True, capture_output=True, timeout=300, shell=False)
        pages = []
        for name in sorted(os.listdir(tmp)):
            img = QImage(os.path.join(tmp, name))
            if not img.isNull():
                pages.append(_to_b64(img))
        return pages or None
    except (subprocess.SubprocessError, OSError):
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pdf_pages(path, dpi=200):
    for reader in (_pdf_qt, _pdf_gdal, _pdf_poppler):
        try:
            pages = reader(path, dpi)
        except Exception:
            pages = None
        if pages:
            return pages
    raise DeedReadError("This copy of QGIS can't turn PDF pages into images. Save the deed as PNG, JPG or TIF, "
                        "or install Poppler (pdftoppm).")


def load(path, dpi=200):
    """Page images as base64 PNG. Embedded PDF text is not returned."""
    if os.path.splitext(path)[1].lower() == ".pdf":
        return pdf_pages(path, dpi)
    return image_pages(path)
