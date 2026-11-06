# Portable DOCX CJK font embedding

Implemented independent `services/api/app/section/docx_fonts.py` and `services/api/tests/test_section_docx_fonts.py`. No edits to shared export.py/render_helper.py, no font installation, no source calculation DOCX modification. Core module and tests frozen for root integration.

## API

```python
from app.section.docx_fonts import embed_document_font
metadata = embed_document_font(document, source_font_path)
document.save(output_path)
```

Alternatively pass an existing DOCX path; the function rewrites that path atomically. A python-docx Document is modified in memory. Existing run/style fonts must select the embedded family's name; the function does not alter document text/style selection.

Returned metadata: `family`, `sourceSha256`, `sourceBytes`, `fsType`, `subsetted`, `fontKey`, `partName`, `contentType`.

## OOXML implementation

The complete source OpenType/TrueType font is embedded in `/word/fonts/pitguard-{sha-prefix}.odttf`. A font-table relationship points to it with relationship type `.../relationships/font`. Font table contains the family entry and `w:embedRegular` with `r:id`, `w:fontKey`, `w:subsetted=false`. Content type is `application/vnd.openxmlformats-officedocument.obfuscatedFont`.

A deterministic GUID derives from the font SHA256; its16 bytes are reversed and XORed twice into bytes0–31. Every byte after offset31 stays untouched. Reapplying the same XOR reconstructs the exact original font file, including all glyphs and original license/name tables. Idempotent repeated calls retain one embedded font part.

Settings explicitly enable embedded TrueType/system fonts and disable subset-only saving. No machine fonts are registered or installed.

Permission checks inspect OS/2 fsType before any document mutation. Restricted, bitmap-only and print/preview-only fonts are rejected for this editable DOCX use. No-subsetting rights are honored by always embedding the entire font.

Primary references:
- Microsoft font part specification: https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oe376/1663dabc-5d98-463f-889e-bcd9b77c3d34
- Microsoft OS/2 fsType specification: https://learn.microsoft.com/en-us/typography/opentype/spec/os2#fstype
- ECMA-376 font-embedding algorithm reproduced at https://c-rex.net/samples/ooxml/e1/Part4/OOXML_P4_DOCX_Font_topic_ID0ERNCU.html

## Tests

Six tests were written and observed failing before implementation. Final scoped command:

```sh
PYTHONPATH=services/api /workspace/scratch/fd5d8a28c64e/work/.venv/bin/python -m pytest services/api/tests/test_section_docx_fonts.py -q
```

Result: **6 passed in3.65s**. Coverage: required fontTable relationship/content type/settings, independently decoded exact full-font bytes, idempotent path embedding without text change, restricted/preview/bitmap rights rejection without mutation, and no-subsetting full-byte embedding. Root subsequently reports its integrated export+font scope15 tests passing.

## Real headless render

Copied the supplied existing calculation.docx to:
`/workspace/scratch/fd5d8a28c64e/work/docx_embedded_font_review/calculation_embedded.docx`

Embedded repository font `NotoSansCJKsc-Regular.otf`, family `Noto Sans CJK SC`, fsType0, full16,437,364 bytes. Font SHA256:
`2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b`

Confirmed original calculation.docx hash remained unchanged. Ran primary-runtime document renderer:

```sh
"$CODEX_PRIMARY_RUNTIME_PYTHON" /root/.codex/skills/builtins/documents/render_docx.py \
 /workspace/scratch/fd5d8a28c64e/work/docx_embedded_font_review/calculation_embedded.docx \
 --output_dir /workspace/scratch/fd5d8a28c64e/work/docx_embedded_font_review/rendered --emit_pdf
```

Result: **16-page PDF and16 page PNGs**. PDF font table contains embedded NotoSansCJKsc-Regular; all16 pages contain extractable Chinese. Per-page CJK counts:326,476,285,310,99,112,445,357,357,414,249,330,299,355,504,583. Pages1,8,16 were visually inspected at full preview; Chinese titles, paragraphs, tables and footers are visible. Root will regenerate from final source and perform final layout QA; this sample proves font recovery rather than asserting final-layout approval.

Files: `rendered/calculation_embedded.pdf`, `rendered/page-1.png` through `page-16.png`, and `render_verification.json`. Embedded DOCX size13,901,837 bytes; complete font deliberately increases document size in exchange for editable glyph completeness. No subsetting optimization was attempted.
