"""Portable OOXML font embedding without installing a machine font.

ECMA-376 font obfuscation: reverse the 16 GUID bytes and XOR them into
font bytes0..31 twice. Full unmodified font bytes preserve all glyphs/notices.
Microsoft font part content type:
https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oe376/1663dabc-5d98-463f-889e-bcd9b77c3d34
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID
import os

W='http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
FONT_CONTENT_TYPE='application/vnd.openxmlformats-officedocument.obfuscatedFont'
FONT_TABLE_CONTENT_TYPE='application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml'


def embed_document_font(document_or_path, source_path) -> dict:
    """Embed one complete editable font into a python-docx Document or DOCX path.

    A Document is updated in memory and must subsequently be saved by its owner.
    A path is rewritten atomically only after source permission checks succeed.
    This does not install fonts, subset glyphs, convert outlines or change text.
    The existing document must select this font family in its run/style fonts.
    """
    from docx import Document
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from fontTools.ttLib import TTFont
    from lxml import etree

    source=Path(source_path)
    raw=source.read_bytes()
    if len(raw)<32 or raw[:4] not in (b'OTTO',b'\x00\x01\x00\x00',b'true'):
        raise ValueError('Font embedding requires a standalone OpenType/TrueType font')
    with TTFont(source,lazy=True) as font:
        if 'OS/2' not in font:
            raise ValueError('Font embedding permission is unavailable (missing OS/2)')
        permission=int(font['OS/2'].fsType)
        # Editable DOCX must not silently downgrade to print/preview-only rights.
        if permission & 0x0002:
            raise ValueError('Font embedding is restricted by OS/2 fsType')
        if permission & 0x0200:
            raise ValueError('Font permits bitmap embedding only')
        if permission & 0x0004 and not permission & 0x0008:
            raise ValueError('Font does not permit editable embedding')
        family=font['name'].getDebugName(1)
        if not family:
            raise ValueError('Embedded font has no family name')
    digest=sha256(raw).hexdigest()
    # Deterministic GUID permits reproducible content and idempotent relationships.
    key=UUID(hex=digest[:32]); mask=key.bytes[::-1]
    obfuscated=bytearray(raw)
    for i in range(32): obfuscated[i]^=mask[i%16]
    path=Path(document_or_path) if isinstance(document_or_path,(str,os.PathLike)) else None
    document=Document(path) if path is not None else document_or_path
    if not hasattr(document,'part') or not hasattr(document,'settings'):
        raise TypeError('Expected python-docx Document or DOCX path')
    package=document.part.package
    try:
        table_part=document.part.part_related_by(R+'/fontTable')
        table=etree.fromstring(table_part.blob)
    except KeyError:
        table=etree.Element('{'+W+'}fonts',nsmap={'w':W,'r':R})
        table_part=Part(PackURI('/word/fontTable.xml'),FONT_TABLE_CONTENT_TYPE,b'',package)
        document.part.relate_to(table_part,R+'/fontTable')
    name=PackURI('/word/fonts/pitguard-'+digest[:24]+'.odttf')
    font_part=next((rel.target_part for rel in table_part.rels.values()
                    if not rel.is_external and rel.target_part.partname==name),None)
    if font_part is None:
        font_part=Part(name,FONT_CONTENT_TYPE,bytes(obfuscated),package)
    elif font_part.blob!=bytes(obfuscated):
        raise ValueError('Embedded font part conflicts with its source hash')
    relation=table_part.relate_to(font_part,R+'/font')
    entry=next((element for element in table.findall('{'+W+'}font')
                if element.get('{'+W+'}name')==family),None)
    if entry is None:
        entry=etree.SubElement(table,'{'+W+'}font');entry.set('{'+W+'}name',family)
    regular=entry.find('{'+W+'}embedRegular')
    if regular is None: regular=etree.SubElement(entry,'{'+W+'}embedRegular')
    regular.set('{'+R+'}id',relation)
    regular.set('{'+W+'}fontKey','{'+str(key).upper()+'}')
    regular.set('{'+W+'}subsetted','false')
    table_part._blob=etree.tostring(table,xml_declaration=True,encoding='UTF-8',standalone=True)
    settings=document.settings.element
    for local,value in (('embedTrueTypeFonts','true'),('embedSystemFonts','true'),('saveSubsetFonts','false')):
        element=settings.find('{'+W+'}'+local)
        if element is None: element=etree.SubElement(settings,'{'+W+'}'+local)
        element.set('{'+W+'}val',value)
    if path is not None:
        temporary=None
        try:
            with NamedTemporaryFile(prefix=path.stem+'-',suffix='.docx',dir=path.parent,delete=False) as handle:
                temporary=Path(handle.name)
            document.save(temporary)
            os.replace(temporary,path)
        finally:
            if temporary is not None and temporary.exists(): temporary.unlink()
    return {'family':family,'sourceSha256':digest,'sourceBytes':len(raw),'fsType':permission,
            'subsetted':False,'fontKey':'{'+str(key).upper()+'}','partName':str(name),
            'contentType':FONT_CONTENT_TYPE}
