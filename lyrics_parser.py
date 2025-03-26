import re
from dataclasses import dataclass
from typing import Dict, Generic, List, Literal, Optional, TypeVar, Union, cast

# Type definitions
SkType = TypeVar("SkType", bound=str)
NCMLrcGroupNameType = Literal["main", "roma", "trans", "meta"]
NCM_MAIN_LRC_GROUP: NCMLrcGroupNameType = "main"

@dataclass
class LrcLine:
    time: int
    """Lyric Time (ms)"""
    lrc: str
    """Lyric Content"""
    skip_merge: bool = False


@dataclass
class LrcGroupLine(Generic[SkType]):
    time: int
    """Lyric Time (ms)"""
    lrc: Dict[SkType, str]


# Regular expressions for parsing LRC format
LRC_TIME_REGEX = r"(?P<min>\d+):(?P<sec>\d+)([\.:](?P<mili>\d+))?(-(?P<meta>\d))?"
LRC_LINE_REGEX = re.compile(rf"^((\[{LRC_TIME_REGEX}\])+)(?P<lrc>.*)$", re.MULTILINE)


def parse_lrc(
    lrc: str,
    ignore_empty: bool = False,
    merge_empty: bool = True,
) -> List[LrcLine]:
    """Parse LRC format lyrics with timestamps."""
    parsed = []
    for line in re.finditer(LRC_LINE_REGEX, lrc):
        lrc_text = line["lrc"].strip().replace("\u3000", " ")
        times = [x.groupdict() for x in re.finditer(LRC_TIME_REGEX, line[0])]

        parsed.extend(
            [
                LrcLine(
                    time=(
                        int(i["min"]) * 60 * 1000
                        + int(float(f"{i['sec']}.{i['mili'] or 0}") * 1000)
                    ),
                    lrc=lrc_text,
                    skip_merge=bool(i["meta"])
                    or lrc_text.startswith(("作词", "作曲", "编曲")),
                )
                for i in times
            ],
        )

    if ignore_empty:
        parsed = [x for x in parsed if x.lrc]

    elif merge_empty:
        new_parsed = []

        for line in parsed:
            if line.lrc or (new_parsed and new_parsed[-1].lrc and (not line.lrc)):
                new_parsed.append(line)

        if new_parsed and (not new_parsed[-1].lrc):
            new_parsed.pop()

        parsed = new_parsed

    parsed.sort(key=lambda x: x.time)
    return parsed


def strip_lrc_lines(lines: List[LrcLine]) -> List[LrcLine]:
    """Strip whitespace from lyrics lines."""
    for lrc in lines:
        lrc.lrc = lrc.lrc.strip()
    return lines


def merge_lrc(
    lyric_groups: Dict[SkType, List[LrcLine]],
    main_group: Optional[SkType] = None,
    threshold: int = 20,
    replace_empty_line: Optional[str] = None,
    skip_merge_group_name: Optional[SkType] = None,
) -> List[LrcGroupLine[SkType]]:
    """Merge different lyrics groups (original, translation, etc.) with time synchronization."""
    lyric_groups = {k: v.copy() for k, v in lyric_groups.items()}
    
    # Clean up empty lines at the end
    for v in lyric_groups.values():
        while v and not v[-1].lrc:
            v.pop()

    if main_group is None:
        # Use first group as main if not specified
        main_group, main_lyric = next(iter(lyric_groups.items()))
    else:
        main_lyric = lyric_groups[main_group]
    
    main_lyric = strip_lrc_lines(main_lyric)

    # Process other groups
    lyric_groups.pop(main_group)
    sub_lines = [(n, strip_lrc_lines(x)) for n, x in lyric_groups.items()]

    if replace_empty_line:
        for x in main_lyric:
            if not x.lrc:
                x.lrc = replace_empty_line
                x.skip_merge = True

    # Merge lines with time synchronization
    merged: List[LrcGroupLine] = []
    for main_line in main_lyric:
        if not main_line.lrc:
            continue

        main_time = main_line.time
        line_main_group = (
            skip_merge_group_name
            if main_line.skip_merge and skip_merge_group_name
            else main_group
        )
        line_group = LrcGroupLine(
            time=main_time,
            lrc={line_main_group: main_line.lrc},
        )

        for group, sub_lrc in sub_lines:
            for i, line in enumerate(sub_lrc):
                if (not line.lrc) or main_line.skip_merge:
                    continue

                if (main_time - threshold) <= line.time < (main_time + threshold):
                    for _ in range(i + 1):
                        it = sub_lrc.pop(0)
                        if it.lrc:
                            line_group.lrc[group] = it.lrc
                    break

        merged.append(line_group)

    # Handle any remaining lines from sub groups
    if sub_lines:
        rest_lrc_len = max((len(x[1]) for x in sub_lines), default=0)
        if rest_lrc_len:
            extra_lines = [
                LrcGroupLine(time=merged[-1].time + 1000, lrc={})
                for _ in range(rest_lrc_len)
            ]
            for group, line in sub_lines:
                for target, extra in zip(extra_lines, line):
                    target.lrc[group] = extra.lrc
            merged.extend(extra_lines)

    return merged


def process_lyrics(
    original_lyrics: str, 
    translation_lyrics: Optional[str] = None,
    roma_lyrics: Optional[str] = None
) -> List[LrcGroupLine[NCMLrcGroupNameType]]:
    """Process lyrics including original, translation and romaji if available."""
    lyrics_groups: Dict[NCMLrcGroupNameType, List[LrcLine]] = {}
    
    # Parse original lyrics
    if original_lyrics and (original_lrc := parse_lrc(original_lyrics)):
        lyrics_groups["main"] = original_lrc
    
    # Parse translation if available
    if translation_lyrics and (trans_lrc := parse_lrc(translation_lyrics)):
        lyrics_groups["trans"] = trans_lrc
    
    # Parse romaji if available
    if roma_lyrics and (roma_lrc := parse_lrc(roma_lyrics)):
        lyrics_groups["roma"] = roma_lrc
    
    # If no valid lyrics were parsed, return empty list
    if not lyrics_groups:
        return []
    
    # Merge all lyrics groups
    return merge_lrc(
        lyrics_groups,
        main_group="main",
        threshold=1000,  # More flexible time matching
        skip_merge_group_name="meta",
    ) 