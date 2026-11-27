from __future__ import annotations

import lzma
import re
import zlib
from typing import BinaryIO
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

DOCUMENT_SUFFIXES = frozenset({".docx", ".odt", ".pptx", ".xlsx"})
MEMBER = re.compile(r"word/(?:document|header\d+|footer\d+|footnotes|endnotes|comments)\.xml|ppt/(?:slides/slide|notesSlides/notesSlide)\d+\.xml|xl/(?:sharedStrings|worksheets/sheet\d+)\.xml|content\.xml")


def _paragraphs(handle: BinaryIO):
    depth = 0
    for event, element in ElementTree.iterparse(handle, events=("start", "end")):
        tag = element.tag.rsplit("}", 1)[-1]
        paragraph = tag in {"p", "h", "si", "is"} or tag == "c" and element.get("t") == "str"
        if event == "start":
            depth += paragraph
            continue
        if paragraph:
            depth -= 1
        if not depth:
            if paragraph or tag == "t":
                yield "".join(element.itertext())
            element.clear()


def extract(handle: BinaryIO, limit: int) -> str | None:
    try:
        handle.seek(0)
        with ZipFile(handle) as archive:
            members = sorted(name for name in archive.namelist() if MEMBER.fullmatch(name))
            if not members or sum(archive.getinfo(name).file_size for name in members) > limit:
                return None
            lines = []
            for name in members:
                with archive.open(name) as entry:
                    lines.extend(_paragraphs(entry))
            return "\n".join(lines)
    except (BadZipFile, ElementTree.ParseError, KeyError, EOFError, RuntimeError, NotImplementedError,
            OSError, ValueError, zlib.error, lzma.LZMAError):
        return None
