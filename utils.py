import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import jinja2
from nonebot_plugin_htmlrender import get_new_page
from .lyrics_parser import process_lyrics, LrcGroupLine, NCMLrcGroupNameType

# Initialize Jinja2 environment
template_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(Path(__file__).parent / "templates")),
    autoescape=jinja2.select_autoescape(["html", "xml"]),
    enable_async=True
)

class LyricsLine:
    def __init__(self, text: str, translation: Optional[str] = None):
        self.text = text
        self.translation = translation

def parse_lyrics(lyric_text: str) -> List[LyricsLine]:
    """Parse lyrics text and remove timestamps."""
    # Remove timestamps like [00:00.000]
    lines = []
    timestamp_pattern = r'\[\d{2}:\d{2}(\.\d{2,3})?\]'
    
    for line in lyric_text.strip().split('\n'):
        # Skip empty lines
        cleaned_line = re.sub(timestamp_pattern, '', line).strip()
        if not cleaned_line:
            continue
        
        lines.append(LyricsLine(text=cleaned_line))
    
    return lines

def match_translations(original_lines: List[LyricsLine], translation_text: str) -> List[LyricsLine]:
    """Match translations with original lyrics lines."""
    # Skip if no translation
    if not translation_text:
        return original_lines
    
    # Parse translation lines
    translation_lines = parse_lyrics(translation_text)
    
    # Match translations to original lines (simple approach: assume same number of lines)
    for i, line in enumerate(original_lines):
        if i < len(translation_lines):
            line.translation = translation_lines[i].text
    
    return original_lines

async def render_template(template_name: str, **kwargs) -> str:
    """Render a template with the given context."""
    template = template_env.get_template(template_name)
    return await template.render_async(**kwargs)

async def render_html_to_pic(html: str, selector: str = "main") -> bytes:
    """Render HTML to an image."""
    async with get_new_page() as page:
        await page.set_content(html)
        element = await page.query_selector(selector)
        if element:
            return await element.screenshot(type="png")
        return await page.screenshot(type="png")

async def render_lyrics_to_pic(
    title: str, 
    artist: str, 
    lyrics: str, 
    translation: Optional[str] = None,
    romaji: Optional[str] = None
) -> bytes:
    """Render lyrics to an image with better synchronization between translations."""
    # Process lyrics with the new parser
    lyrics_groups = process_lyrics(
        original_lyrics=lyrics,
        translation_lyrics=translation,
        roma_lyrics=romaji
    )
    
    # Convert to the format expected by the template
    # Create tuples of (type, text) for each line group
    groups = []
    for group in lyrics_groups:
        group_tuples = [(n, r) for n, r in group.lrc.items()]
        # Sort to ensure consistent order: main first, then translation
        sort_order = ("main", "roma", "trans", "meta")
        group_tuples.sort(key=lambda x: sort_order.index(x[0]) if x[0] in sort_order else 999)
        groups.append(group_tuples)
    
    # Render template with the groups
    html = await render_template(
        "lyrics.html.jinja",
        song_title=title,
        song_artist=artist,
        groups=groups
    )
    
    # Render the HTML to an image
    return await render_html_to_pic(html) 