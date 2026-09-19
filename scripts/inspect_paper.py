"""Reproducible local PDF inspection; leaves original documents untouched."""
import json
import sys
from pathlib import Path
import pdfplumber
import pypdfium2 as pdfium

source, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
render = pdfium.PdfDocument(source)
with pdfplumber.open(source) as pdf:
    pages = []
    for i, page in enumerate(pdf.pages):
        text = page.extract_text(x_tolerance=2) or ''
        (out / f'page_{i+1:02}.txt').write_text(text, encoding='utf-8')
        image = render[i].render(scale=1.7).to_pil()
        image.save(out / f'page_{i+1:02}.png')
        pages.append(dict(page=i+1, width=page.width, height=page.height, text_chars=len(text), images=len(page.images), curves=len(page.curves)))
        if i == 2:
            (out / 'page_03_vectors.json').write_text(json.dumps(page.curves, ensure_ascii=False), encoding='utf-8')
    (out / 'inventory.json').write_text(json.dumps(pages, indent=2), encoding='utf-8')
print(json.dumps(pages))
