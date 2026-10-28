"""Niches: what each video is about (Gaming, Finance…), so the window can show one niche at a time.

ClipMonster 2.3+ sorts its clips into niches and says which in clips.json (VideoItem.niche and
niche_id). Older clips, clips added by hand and clips whose clips.json entry was missing don't
say, so a video's niche is worked out, the first of these that gives one:

1. you picked it (Library → right-click → Niche), kept in VideoItem.niche_set;
2. ClipMonster said so, in clips.json or in its list of picked videos (picks.json), under the niche's
   current name when ClipMonster still has it;
3. a folder it's in is named after a niche (ClipMonster puts each niche's clips in <output>/<niche>/);
4. the other clips from the same folder are all one niche;
5. its YouTube channel is in a niche's channel list in ClipMonster, or the other clips from that
   channel are all one niche;
6. the account it's queued for or was posted to is the one TikTok account a niche posts to;
7. ClipMonster found its video on the YouTube charts and only one niche takes chart videos (clips
   from before ClipMonster had niches, when the charts were where its videos came from).

Otherwise it has no niche. Picking a niche only changes what the pages show: every queued video
still posts at its time.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import clipmonster
from .store import VideoItem

ALL = ""  # the filter that shows every video
NONE = "-"  # videos with no niche (as a filter, and as VideoItem.niche_set: "you said it has none")

# Why a video is in its niche (Index.why), shown in the Library's tooltip and details
WHY = {
    "you": "you picked it",
    "clipmonster": "ClipMonster made it for this niche",
    "folder": "from the folder it's in",
    "siblings": "the other clips from its folder are",
    "channel": "its channel is",
    "picked": "ClipMonster picked its video for this niche",
    "account": "the account it's for posts this niche",
    "charts": "ClipMonster found its video on the YouTube charts, and only this niche takes chart videos",
}


@dataclass
class Niche:
    key: str  # ClipMonster's niche id, or "name:<folded name>" for one only known by name
    name: str
    account: str = ""  # the TikTok @username ClipMonster says the niche posts to ("" = none named)
    clipmonster: bool = False  # in ClipMonster's niche list on this PC

    @property
    def label(self) -> str:
        return f"{self.name} (@{self.account})" if self.account else self.name


def fold(text: str) -> str:
    """Letters and digits only, lower case: "Gaming Videos", "gaming_videos" and "GAMING-VIDEOS" match."""
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def _handle(username: str) -> str:
    """A TikTok @username as written, without the "@"."""
    return (username or "").strip().lstrip("@").strip()


# --- what ClipMonster on this PC knows ------------------------------------------------------------


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


_CHANNEL_ID = re.compile(r"\b(UC[\w-]{22})\b")
_CHANNEL_NAME = re.compile(r"(?:^|/)(@[\w.\-]+|(?:c|user)/[\w.\-]+)", re.I)


@dataclass
class ClipMonsterInfo:
    """What ClipMonster on this PC knows about niches."""

    niches: list[Niche] = field(default_factory=list)  # in its order
    channels: dict[str, str] = field(default_factory=dict)  # folded channel name -> niche id (its channel lists)
    videos: dict[str, str] = field(default_factory=dict)  # YouTube video id -> the niche it was picked for
    charts: set[str] = field(default_factory=set)  # video ids it picked from the YouTube charts
    chart_niche: str = ""  # the one switched-on niche that takes chart videos (by category or title words)
    stamp: tuple = ()  # the files it was read from and when they last changed


def clipmonster_info(settings_path: Path | None = None) -> ClipMonsterInfo:
    """ClipMonster's niches, the channels in their channel lists and the videos it picked (read again
    only when one of its files changes: the pages ask on every refresh).

    A channel line is a link like youtube.com/@name, an @name or a channel id; its name comes from
    ClipMonster's research file, which keeps each channel's title once it has read it. picks.json
    has each picked video's niche (since 2.3) and where it was found."""
    path = settings_path or clipmonster.settings_path()
    stamp = (str(path), _mtime(path)) + tuple(_mtime(path.with_name(n)) for n in ("research.json", "picks.json"))
    if _info and _info[0].stamp == stamp:
        return _info[0]
    data = _read_json(path)
    raw = data.get("niches") if isinstance(data, dict) else None
    research = _read_json(path.with_name("research.json"))
    channels = (research.get("sources") or {}).get("channels") if isinstance(research, dict) else None
    channels = channels if isinstance(channels, dict) else {}
    by_id = {str(c.get("id")): str(c.get("title") or "") for c in channels.values() if isinstance(c, dict)}
    info = ClipMonsterInfo(stamp=stamp)
    chart_takers: list[str] = []
    for d in raw if isinstance(raw, list) else []:
        if not isinstance(d, dict):
            continue
        name = " ".join(str(d.get("name") or "").split())
        nid = re.sub(r"[^\w-]", "", str(d.get("id") or "")) or f"name:{fold(name)}"
        if not fold(name) or any(n.key == nid or fold(n.name) == fold(name) for n in info.niches):
            continue
        info.niches.append(Niche(nid, name, _handle(str(d.get("account") or "")), clipmonster=True))
        if d.get("enabled", True) and (str(d.get("categories") or "").strip() or str(d.get("keywords") or "").strip()):
            chart_takers.append(nid)
        for line in str(d.get("channels") or "").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            names = set()
            by_cid, by_name = _CHANNEL_ID.search(line), _CHANNEL_NAME.search(line)
            if by_cid:
                names.add(by_id.get(by_cid.group(1), ""))
            if by_name:
                key = by_name.group(1).lower()
                key = key if key.startswith("@") else "@" + key.split("/", 1)[1]
                known = channels.get(key)
                names |= {key[1:], str(known.get("title") or "") if isinstance(known, dict) else ""}
            elif not by_cid and "/" not in line:
                names.add(line)  # a bare channel name
            for n in names:
                if fold(n):
                    info.channels.setdefault(fold(n), nid)
    info.chart_niche = chart_takers[0] if len(chart_takers) == 1 else ""
    picks = _read_json(path.with_name("picks.json"))
    for p in (picks.get("picks") if isinstance(picks, dict) else None) or []:
        if not isinstance(p, dict) or not p.get("id"):
            continue
        vid = str(p["id"])
        if p.get("niche"):
            info.videos[vid] = str(p["niche"])
        if "chart" in (p.get("origins") or []):
            info.charts.add(vid)
    _info[:] = [info]
    return info


_info: list[ClipMonsterInfo] = []  # the last one read


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


_VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/)([\w-]{11})")


def video_id(it: VideoItem) -> str:
    """The YouTube id of the video a clip was cut from, if ClipMonster said."""
    if re.fullmatch(r"[\w-]{11}", it.source_group or ""):
        return it.source_group
    m = _VIDEO_ID.search(it.source_url or "")
    return m.group(1) if m else ""


# --- the index ----------------------------------------------------------------------------------


class Index:
    """Every video's niche, worked out once for the whole library (rules 4 to 7 look at the other
    clips), and the niches there are: ClipMonster's first, in its order, then the rest by name."""

    def __init__(self, items: Iterable[VideoItem], accounts: Iterable = (), watch_folders: Iterable[str] = (),
                 cm: ClipMonsterInfo | None = None):
        items = list(items)
        cm = cm or ClipMonsterInfo()
        self.cm = cm
        self.niches: list[Niche] = []
        self._by_key: dict[str, Niche] = {}
        self._by_fold: dict[str, str] = {}  # folded name -> key
        for n in cm.niches:
            self._add(n)
        self._roots = [_path(f) for f in watch_folders if f]
        self._of: dict[str, tuple[str, str]] = {}  # item id -> (key, why)

        # Names ClipMonster no longer has (a deleted niche) or never had (picked by hand) still count.
        extra: list[Niche] = []
        for it in items:
            for nid, name, account in ((it.niche_id, it.niche, it.for_account), ("", it.niche_set, "")):
                name = " ".join((name or "").split())
                if not fold(name) or name == NONE or (nid and nid in self._by_key) or fold(name) in self._by_fold:
                    continue
                n = Niche(nid or f"name:{fold(name)}", name, _handle(account))
                self._add(n)
                extra.append(n)
        self.niches = [n for n in self.niches if n not in extra] + sorted(extra, key=lambda n: n.name.casefold())

        # Rules 1-3 need only the video itself; the rest then fill the gaps from what the others are.
        for it in items:
            got = self._direct(it)
            if got:
                self._of[it.id] = got
        by_folder: dict[str, set[str]] = {}
        by_channel: dict[str, set[str]] = {}
        for it in items:
            key = self._of.get(it.id, ("", ""))[0]
            if key and key != NONE:
                by_folder.setdefault(_folder(it), set()).add(key)
                if fold(it.source_channel):
                    by_channel.setdefault(fold(it.source_channel), set()).add(key)
        for_account: dict[str, str] = {}
        for a in accounts:
            want = _handle(getattr(a, "username", "")).lower()
            hits = {n.key for n in self.niches if want and n.account.lower() == want}
            if len(hits) == 1:
                for_account[a.id] = hits.pop()
        chart = cm.chart_niche if cm.chart_niche in self._by_key else ""
        for it in items:
            if it.id in self._of:
                continue
            siblings = by_folder.get(_folder(it), set())
            channel = fold(it.source_channel)
            listed = cm.channels.get(channel, "") if channel else ""
            if len(siblings) == 1:
                self._of[it.id] = (next(iter(siblings)), "siblings")
            elif listed in self._by_key:
                self._of[it.id] = (listed, "channel")
            elif channel and len(by_channel.get(channel, ())) == 1:
                self._of[it.id] = (next(iter(by_channel[channel])), "channel")
            elif it.account in for_account:
                self._of[it.id] = (for_account[it.account], "account")
            elif chart and video_id(it) in cm.charts:
                self._of[it.id] = (chart, "charts")

    def _add(self, n: Niche) -> None:
        if n.key in self._by_key or fold(n.name) in self._by_fold:
            return
        self.niches.append(n)
        self._by_key[n.key] = n
        self._by_fold[fold(n.name)] = n.key

    def _named(self, name: str) -> str:
        return self._by_fold.get(fold(name), "")

    def _direct(self, it: VideoItem) -> tuple[str, str] | None:
        if it.niche_set:
            return (NONE, "you") if it.niche_set == NONE else (self._named(it.niche_set), "you")
        if it.niche_id and it.niche_id in self._by_key:
            return it.niche_id, "clipmonster"
        if it.niche and self._named(it.niche):
            return self._named(it.niche), "clipmonster"
        picked = self.cm.videos.get(video_id(it), "")
        if picked in self._by_key:
            return picked, "picked"
        for part in self._folder_parts(it.path):
            key = self._named(part)
            if key:
                return key, "folder"
        return None

    def _folder_parts(self, path: str) -> list[str]:
        """The folders a video is in, nearest first; inside a watched folder, only that folder and those
        below it (whatever the folders around it are called)."""
        p = _path(path)
        parents = list(p.parents)[:-1]  # not the drive
        for root in self._roots:
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            parents = [root / q for q in rel.parents]
            break
        return [q.name for q in parents if q.name]

    # --- asking it --------------------------------------------------------------------------

    def of(self, it: VideoItem) -> str:
        """The video's niche key, NONE when you said it has none, "" when none could be worked out."""
        got = self._of.get(it.id) or self._direct(it)
        return got[0] if got else ""

    def why(self, it: VideoItem) -> str:
        got = self._of.get(it.id) or self._direct(it)
        return WHY.get(got[1], "") if got and got[0] != NONE else ""

    def get(self, key: str) -> Niche | None:
        return self._by_key.get(key)

    def name(self, key: str) -> str:
        n = self._by_key.get(key)
        return n.name if n else ""

    def name_of(self, it: VideoItem) -> str:
        return self.name(self.of(it))

    def matches(self, it: VideoItem, want: str) -> bool:
        """Does the video belong under the filter `want` (ALL, NONE or a niche key)?"""
        if want == ALL:
            return True
        key = self.of(it)
        return key in ("", NONE) if want == NONE else key == want

    def keep(self, items: Iterable[VideoItem], want: str) -> list[VideoItem]:
        return [it for it in items if self.matches(it, want)]

    def counts(self, items: Iterable[VideoItem]) -> dict[str, int]:
        """How many of `items` are in each niche, NONE for those in none."""
        out: dict[str, int] = {}
        for it in items:
            key = self.of(it) or NONE
            out[key] = out.get(key, 0) + 1
        return out

    def valid(self, want: str) -> bool:
        return want in (ALL, NONE) or want in self._by_key


def _path(text: str) -> Path:
    """A path from the library, which may have been saved on Windows (backslashes)."""
    return Path(str(text).replace("\\", "/"))


def _folder(it: VideoItem) -> str:
    return str(_path(it.path).parent).casefold()


def build(items: Iterable[VideoItem], accounts: Iterable = (), watch_folders: Iterable[str] = (),
          settings_path: Path | None = None) -> Index:
    """The index for the library, with ClipMonster's niches on this PC if it has any."""
    return Index(items, accounts, watch_folders, clipmonster_info(settings_path))


def set_niche(items: Iterable[VideoItem], name: str) -> int:
    """You say what niche these videos are: a niche's name, NONE for none, "" to work it out again.
    Returns how many changed."""
    name = " ".join((name or "").split())
    n = 0
    for it in items:
        if it.niche_set != name:
            it.niche_set = name
            n += 1
    return n


def merge_order(order: list[str], shown: list[str]) -> list[str]:
    """A queue's full order after the videos in `shown` (one niche's, say) were put in that order: they
    take the places the shown videos had, and the hidden ones stay where they were."""
    present = set(order)
    shown = [i for i in shown if i in present]
    wanted = set(shown)
    it = iter(shown)
    return [next(it) if i in wanted else i for i in order]
