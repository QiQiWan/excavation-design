from __future__ import annotations

import importlib
from io import BytesIO
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

import pytest
from docx import Document
from lxml import etree
from fontTools.ttLib import TTFont

FONT=Path(__file__).resolve().parents[1]/'app/section/assets/NotoSansCJKsc-Regular.otf'
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'p':'http://schemas.openxmlformats.org/package/2006/relationships',
    'ct':'http://schemas.openxmlformats.org/package/2006/content-types'}


def api():
    try: return importlib.import_module('app.section.docx_fonts')
    except ModuleNotFoundError: pytest.fail('Portable DOCX font embedding is not implemented')


def test_embedded_regular_font_relationship_and_bytes_are_complete_and_reversible():
    document=Document();document.add_paragraph('原项目条件 基坑支护设计 全阶段曲线')
    info=api().embed_document_font(document,FONT)
    out=BytesIO();document.save(out)
    with ZipFile(out) as package:
        table=etree.fromstring(package.read('word/fontTable.xml'))
        embedded=table.xpath('//w:font[@w:name="Noto Sans CJK SC"]/w:embedRegular',namespaces=NS)
        assert len(embedded)==1
        relation=embedded[0].get('{'+NS['r']+'}id')
        key=embedded[0].get('{'+NS['w']+'}fontKey')
        rels=etree.fromstring(package.read('word/_rels/fontTable.xml.rels'))
        rel=rels.xpath('//p:Relationship[@Id="'+relation+'"]',namespaces=NS)[0]
        assert rel.get('Type')==NS['r']+'/font'
        name='word/'+rel.get('Target')
        data=bytearray(package.read(name));key_bytes=UUID(key).bytes[::-1]
        for i in range(32):data[i]^=key_bytes[i%16]
        assert bytes(data)==FONT.read_bytes()
        assert package.read(name)[:32]!=FONT.read_bytes()[:32]
        assert package.read(name)[32:]==FONT.read_bytes()[32:]
        types=etree.fromstring(package.read('[Content_Types].xml'))
        assert types.xpath('//ct:Override[@PartName="/'+name+'"]/@ContentType',namespaces=NS)==[
            'application/vnd.openxmlformats-officedocument.obfuscatedFont']
        settings=etree.fromstring(package.read('word/settings.xml'))
        assert settings.find('w:embedTrueTypeFonts',NS) is not None
    assert info['fsType']==0
    assert info['subsetted'] is False
    assert info['family']=='Noto Sans CJK SC'


def test_path_embedding_is_idempotent_and_does_not_change_document_text(tmp_path):
    path=tmp_path/'portable.docx';doc=Document();doc.add_paragraph('中文不应消失');doc.save(path)
    api().embed_document_font(path,FONT)
    api().embed_document_font(path,FONT)
    assert Document(path).paragraphs[0].text=='中文不应消失'
    with ZipFile(path) as z:
        assert len([n for n in z.namelist() if n.endswith('.odttf')])==1


@pytest.mark.parametrize('permission',[2,4,0x200])
def test_restricted_preview_only_or_bitmap_font_cannot_be_embedded_for_editing(tmp_path,permission):
    restricted=tmp_path/'restricted.otf'
    font=TTFont(FONT);font['OS/2'].fsType=permission;font.save(restricted);font.close()
    document=Document()
    with pytest.raises(ValueError,match='embedding|editable|bitmap'):
        api().embed_document_font(document,restricted)
    out=BytesIO();document.save(out)
    with ZipFile(out) as z: assert not any(n.endswith('.odttf') for n in z.namelist())


def test_no_subsetting_permission_is_honored_by_full_byte_embedding(tmp_path):
    source=tmp_path/'full-only.otf'
    font=TTFont(FONT);font['OS/2'].fsType=0x100;font.save(source);font.close()
    assert api().embed_document_font(Document(),source)['subsetted'] is False
