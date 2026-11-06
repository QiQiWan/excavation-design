# Section export CJK font

File: `NotoSansCJKsc-Regular.otf`

- Upstream: https://github.com/notofonts/noto-cjk
- Upstream file: https://github.com/notofonts/noto-cjk/blob/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf
- Release family: Noto Sans CJK 2.004
- Embedded version: `Version 2.004;hotconv 1.0.118;makeotfexe 2.5.65603`
- Copyright from the unmodified font: `© 2014-2021 Adobe (http://www.adobe.com/).`
- License: SIL Open Font License 1.1, supplied in `OFL.txt`.
- Official license source: https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/LICENSE
- File size: 16,437,364 bytes
- SHA-256: `2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b`

The bundled OTF is unmodified. `render_helper.prepare_render_font` makes a
temporary subset and converts its CFF curves to TrueType quadratic outlines
using FontTools. The converted font is named `PitGuard Section Subset`, retains
the original copyright/license metadata, and is embedded into PDF documents.
Its intermediate file is not separately distributed with the calculation package.
Rendering never downloads fonts. Missing required Chinese glyphs cause an error.

Runtime dependencies are FontTools (already required by Matplotlib), ReportLab,
Matplotlib and the bundled font. Package data must include `app.section/assets/*`
when the API is built as a wheel. The unmodified font remains under OFL 1.1 and
must be redistributed together with this notice and `OFL.txt`.
