"""Parse the FATF "call for action" and "increased monitoring" pages.

The FATF website blocks automated downloads, so the pages are saved from a
browser into `data/raw/fatf/` and parsed here. Nothing is typed in by hand:
each jurisdiction, its measure, the list date and the source URL all come
from the saved page.

The pages list one jurisdiction per heading, grouped under section headings
that say which measure applies. Any heading that is neither a known section,
a known non-jurisdiction heading (`fatf.ignore_headings` in the settings) nor
a jurisdiction is reported, so a change in the page layout stops the build
instead of being silently skipped.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser

# Measure values, as stored in raw.fatf_lists.measure.
COUNTERMEASURES = "countermeasures"
ENHANCED_DUE_DILIGENCE = "enhanced_due_diligence"
INCREASED_MONITORING = "increased_monitoring"
REMOVED = "removed"  # on the page only as "no longer subject to increased monitoring"

# Section headings that switch the measure for the headings that follow.
SECTION_PATTERNS = (
    (re.compile(r"no longer subject to increased monitoring", re.IGNORECASE), REMOVED),
    (
        re.compile(r"apply enhanced due diligence", re.IGNORECASE),
        ENHANCED_DUE_DILIGENCE,
    ),
    (re.compile(r"apply countermeasures", re.IGNORECASE), COUNTERMEASURES),
)

# For each FATF list: a phrase its page title must contain, and the measure
# that applies before any section heading.
LIST_TYPES = {
    "call_for_action": {"title": "call for action", "initial_measure": None},
    "increased_monitoring": {
        "title": "increased monitoring",
        "initial_measure": INCREASED_MONITORING,
    },
}

TITLE_DATE = re.compile(r"(\d{1,2} [A-Za-z]+ \d{4})\s*$")


def normalise_name(name: str) -> str:
    """Return a name for matching: lower case, single spaces, straight apostrophes."""
    return " ".join(name.replace("’", "'").split()).casefold()


@dataclass(frozen=True)
class FatfPage:
    """What one saved FATF page says."""

    list_name: str
    title: str
    list_date: date
    source_url: str
    listed: list[tuple[str, str]]  # (jurisdiction as written, measure)
    removed: list[str] = field(default_factory=list)


class _HeadingParser(HTMLParser):
    """Collect every h1-h4 heading (level, text) and the canonical URL."""

    def __init__(self) -> None:
        super().__init__()
        self.headings: list[tuple[int, str]] = []
        self.canonical_url: str | None = None
        self._level: int | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "link" and dict(attrs).get("rel") == "canonical":
            self.canonical_url = dict(attrs).get("href")
        if tag in ("h1", "h2", "h3", "h4"):
            self._level, self._text = int(tag[1]), []

    def handle_endtag(self, tag: str) -> None:
        if self._level is not None and tag == f"h{self._level}":
            self.headings.append((self._level, " ".join("".join(self._text).split())))
            self._level = None

    def handle_data(self, data: str) -> None:
        if self._level is not None:
            self._text.append(data)


def parse_fatf_page(html: str, list_name: str, ignore_headings: list[str]) -> FatfPage:
    """Read the jurisdictions, their measure, the list date and the source URL.

    Args:
        html: the saved page.
        list_name: which list the page should be (a key of `LIST_TYPES`).
        ignore_headings: headings known not to be jurisdictions.

    Raises:
        ValueError: if the page is not the expected list, has no date or
            canonical URL, lists a jurisdiction twice, or a heading appears
            before any section says which measure applies.
    """
    if list_name not in LIST_TYPES:
        raise ValueError(f"Unknown FATF list {list_name!r}")
    spec = LIST_TYPES[list_name]

    parser = _HeadingParser()
    parser.feed(html)
    titles = [text for level, text in parser.headings if level == 1]
    if len(titles) != 1:
        raise ValueError(f"Expected one page title (h1), found {len(titles)}")
    title = titles[0]
    if spec["title"] not in title.casefold():
        raise ValueError(f"Page title {title!r} is not the {list_name} list")
    date_match = TITLE_DATE.search(title)
    if not date_match:
        raise ValueError(f"No list date at the end of the title {title!r}")
    # A calendar date with no time of day, so no time zone applies.
    list_date = datetime.strptime(date_match.group(1), "%d %B %Y").date()  # noqa: DTZ007
    if not parser.canonical_url:
        raise ValueError("Page has no canonical URL (<link rel='canonical'>)")

    ignore = {normalise_name(h) for h in ignore_headings}
    measure = spec["initial_measure"]
    listed: list[tuple[str, str]] = []
    removed: list[str] = []
    after_title = parser.headings[parser.headings.index((1, title)) + 1 :]
    for _, text in after_title:
        if not text or normalise_name(text) in ignore:
            continue
        section = next((m for p, m in SECTION_PATTERNS if p.search(text)), None)
        if section:
            measure = section
        elif measure is None:
            raise ValueError(f"Heading {text!r} comes before any measure section")
        elif measure == REMOVED:
            removed.append(text)
        else:
            listed.append((text, measure))

    names = [normalise_name(n) for n, _ in listed]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(f"Jurisdictions listed twice on one page: {duplicates}")
    if not listed:
        raise ValueError(f"No jurisdictions found on the {list_name} page")

    return FatfPage(
        list_name=list_name,
        title=title,
        list_date=list_date,
        source_url=parser.canonical_url,
        listed=listed,
        removed=removed,
    )
