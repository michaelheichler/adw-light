from pathlib import PurePath
from .markup import MIXED_LANGUAGE_EXTS, RegionKind, _mask_markup, extract_regions, render_regions


def prose_source(path: str, text: str) -> str:
    regions = extract_regions(path, text)
    if PurePath(path.lower()).suffix in MIXED_LANGUAGE_EXTS:
        return render_regions(text, regions, {RegionKind.VISIBLE_PROSE})
    return _mask_markup(path, text)
