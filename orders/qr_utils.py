"""
QR code helpers for production-floor scanning.

Used by the print view to embed a QR code on each work-order sheet.
The QR encodes an absolute URL to that order's /update/ page so the
production floor can scan with any phone camera and land directly on
the action screen for their department.

Returns inline SVG (no separate file storage needed).
"""

from io import BytesIO

import qrcode
import qrcode.image.svg


def generate_qr_svg(data, *, box_size=10, border=2):
    """Build a QR code as an inline SVG string.

    box_size: pixels per QR module (controls overall size; 10 ≈ 3cm at A4 print)
    border: number of blank modules around the code (2 is the QR spec minimum)

    Uses SvgPathImage — renders the QR as a single <path> element so the SVG
    is small and prints sharply.
    """
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(
        data,
        image_factory=factory,
        box_size=box_size,
        border=border,
    )
    buf = BytesIO()
    img.save(buf)
    svg = buf.getvalue().decode('utf-8')

    # Strip the leading <?xml ... ?> declaration so the SVG embeds cleanly
    # inside an HTML document (browsers don't need it for inline SVG).
    if svg.startswith('<?xml'):
        svg = svg.split('?>', 1)[1].lstrip()
    return svg
