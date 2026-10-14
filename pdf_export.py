"""Small Markdown-to-PDF exporter for itinerary headings, lists, and tables."""
import re
from html import escape
from io import BytesIO
from pathlib import Path

import reportlab
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Embed a font so accented destination names work on any device.
FONT_DIR = Path(reportlab.__file__).parent / 'fonts'
pdfmetrics.registerFont(TTFont('Safar', str(FONT_DIR / 'Vera.ttf')))
pdfmetrics.registerFont(TTFont('Safar-Bold', str(FONT_DIR / 'VeraBd.ttf')))
pdfmetrics.registerFontFamily('Safar', normal='Safar', bold='Safar-Bold', italic='Safar', boldItalic='Safar-Bold')


def itinerary_pdf(markdown, image=None):
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, title='SafarAI travel plan', leftMargin=40, rightMargin=40)
    styles = getSampleStyleSheet()
    for style in styles.byName.values():
        style.fontName = 'Safar'
    story, rows = [], []

    def paragraph(text, style='BodyText'):
        text = re.sub(r'\[([^]]+)\]\([^)]+\)', r'\1', text)
        text = escape(text)
        text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
        return Paragraph(text, styles[style])

    def flush_table():
        if rows:
            table = Table(rows, colWidths=[document.width / len(rows[0])] * len(rows[0]), repeatRows=1)
            table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e5def5')),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
                ('LINEBELOW', (0, 0), (-1, -1), .4, colors.lightgrey),
            ]))
            story.extend([table, Spacer(1, 12)])
            rows.clear()

    if image:
        story.extend([Image(BytesIO(image), width=240, height=240), Spacer(1, 12)])
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith('|'):
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            if not all(re.fullmatch(r':?-+:?', cell) for cell in cells):
                rows.append([paragraph(cell) for cell in cells])
            continue
        flush_table()
        if not line:
            story.append(Spacer(1, 6))
            continue
        heading = re.match(r'^(#{1,3})\s+(.+)', line)
        if heading:
            story.append(paragraph(heading[2], 'Heading' + str(len(heading[1]))))
        else:
            line = re.sub(r'^[-*]\s+', '• ', line)
            story.append(paragraph(line))
    flush_table()
    document.build(story)
    return buffer.getvalue()
