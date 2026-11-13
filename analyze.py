"""Rule-based playlist stats. Stdlib only, no model calls."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from fetchers import FetchError
from genres import enrich_tracks, genre_language_stats

# Share / rate fields (topArtists[].share, topAlbums[].share, top3Share,
# collabRate, eras.buckets[].share, genres/languages buckets[].share)
# are decimals in [0, 1]. Example: 0.25 means 25%. The frontend multiplies
# by 100 to display. Genre/language share uses the playlist track count
# as the denominator (same as artist stats).

_TOP_ARTISTS = 10
_TOP_ALBUMS = 5
_ERA_MIN_TRACKS = 8
_ERA_MIN_COVERAGE = 0.30
_YEAR_MIN, _YEAR_MAX = 1900, 2026


def analyze_playlist(
    body: dict,
    *,
    enrich: bool = True,
    cache_path: Path | None = None,
    budget_s: float | None = None,
    fetch_json=None,
) -> dict:
    if not isinstance(body, dict):
        raise FetchError("请求 JSON 必须是对象。")
    tracks = body.get("tracks")
    if not isinstance(tracks, list):
        raise FetchError("请求缺少 tracks 列表，无法分析。")

    # Shallow-copy track dicts so enrichment can attach genres without
    # mutating the caller's request payload.
    tracks = [dict(raw) if isinstance(raw, dict) else raw for raw in tracks]

    title = (body.get("title") or "未命名歌单") if isinstance(body.get("title"), str) else "未命名歌单"
    platform = body.get("platform") or ""
    track_count = len(tracks)
    notes: list[str] = []
    enrich_meta = None
    if enrich and track_count:
        kwargs = {}
        if cache_path is not None:
            kwargs["cache_path"] = cache_path
        if budget_s is not None:
            kwargs["budget_s"] = budget_s
        if fetch_json is not None:
            kwargs["fetch_json"] = fetch_json
        enrich_meta = enrich_tracks(tracks, **kwargs)

    warning = body.get("warning")
    if isinstance(warning, str) and warning.strip():
        notes.append(warning.strip())

    artist_counts: Counter[str] = Counter()
    album_counts: Counter[str] = Counter()
    timed: list[dict] = []
    dated: list[int] = []
    collab_n = 0

    for raw in tracks:
        track = raw if isinstance(raw, dict) else {}
        artists = _clean_artists(track.get("artists"))
        if len(artists) >= 2:
            collab_n += 1
        for name in artists:
            artist_counts[name] += 1

        album = track.get("album")
        if isinstance(album, str) and album.strip():
            album_counts[album.strip()] += 1

        ms = _duration_ms(track.get("durationMs"))
        if ms is not None:
            timed.append({
                "name": str(track.get("name") or "未知曲目"),
                "artists": artists,
                "durationMs": ms,
            })

        year = _track_year(track.get("year"))
        if year is not None:
            dated.append(year)

    unique_artists = len(artist_counts)
    unique_albums = len(album_counts)

    top_artists = [
        {"name": name, "count": count, "share": _ratio(count, track_count)}
        for name, count in artist_counts.most_common(_TOP_ARTISTS)
    ]

    top3_names = [item["name"] for item in top_artists[:3]]
    if track_count and top3_names:
        top3_hits = 0
        for raw in tracks:
            track = raw if isinstance(raw, dict) else {}
            names = set(_clean_artists(track.get("artists")))
            if names.intersection(top3_names):
                top3_hits += 1
        top3_share = _ratio(top3_hits, track_count)
    else:
        top3_share = 0.0

    top_albums = [
        {"name": name, "count": count, "share": _ratio(count, track_count)}
        for name, count in album_counts.most_common(_TOP_ALBUMS)
        if count >= 2
    ]

    duration = _duration_stats(timed, track_count, notes)
    eras = _era_stats(dated, track_count, notes)
    genres, languages = genre_language_stats(
        tracks, track_count, notes, enrich_meta=enrich_meta
    )

    if track_count == 0:
        notes.append("歌单里没有可统计的曲目。")

    return {
        "ok": True,
        "title": title,
        "platform": platform,
        "trackCount": track_count,
        "stats": {
            "trackCount": track_count,
            "uniqueArtists": unique_artists,
            "uniqueAlbums": unique_albums,
            "topArtists": top_artists,
            "top3Share": top3_share,
            "collabRate": _ratio(collab_n, track_count),
            "duration": duration,
            "eras": eras,
            "genres": genres,
            "languages": languages,
            "topAlbums": top_albums,
        },
        "notes": notes,
        "personality": {
            "locked": True,
            "teaser": "基于上面的统计摘要生成一张短人格卡。本期先占位，不调用模型。",
        },
    }


def _clean_artists(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item).strip() if item is not None else ""
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _duration_ms(value: Any) -> int | None:
    if value is None or value is False or value == "":
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    return ms if ms > 0 else None


def _track_year(value: Any) -> int | None:
    if value is None or value is False or value == "":
        return None
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    if _YEAR_MIN <= year <= _YEAR_MAX:
        return year
    return None


def _ratio(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round(part / whole, 4)


def _duration_stats(timed: list[dict], track_count: int, notes: list[str]) -> dict | None:
    if not timed:
        if track_count:
            notes.append("这些曲目没有时长信息，已省略时长统计。")
        return None

    total = sum(item["durationMs"] for item in timed)
    # Stable ties: first extrema in playlist order (timed is in input order).
    longest = max(timed, key=lambda item: (item["durationMs"], -timed.index(item)))
    shortest = min(timed, key=lambda item: (item["durationMs"], timed.index(item)))
    covered = len(timed)
    if covered < track_count:
        notes.append(f"时长统计仅基于有时长的 {covered} 首，其余曲目未提供 durationMs。")
    return {
        "covered": covered,
        "totalMs": total,
        "avgMs": int(round(total / covered)),
        "longest": {
            "name": longest["name"],
            "artists": longest["artists"],
            "durationMs": longest["durationMs"],
        },
        "shortest": {
            "name": shortest["name"],
            "artists": shortest["artists"],
            "durationMs": shortest["durationMs"],
        },
    }


def _era_stats(years: list[int], track_count: int, notes: list[str]) -> dict | None:
    covered = len(years)
    if track_count <= 0 or covered == 0:
        if track_count:
            notes.append("没有可用的发行年份，已省略年代分布（不会猜测年份）。")
        return None

    coverage = covered / track_count
    if covered < _ERA_MIN_TRACKS or coverage < _ERA_MIN_COVERAGE:
        notes.append(
            f"仅 {covered} 首有发行年份（覆盖 {round(coverage * 100)}%），"
            "不足以展示年代分布。"
        )
        return None

    buckets_count: Counter[int] = Counter((year // 10) * 10 for year in years)
    buckets = [
        {
            "label": f"{decade}s",
            "count": count,
            "share": _ratio(count, covered),
        }
        for decade, count in sorted(buckets_count.items())
    ]
    notes.append(f"年代分布基于有发行年份的 {covered} 首。")
    return {"covered": covered, "buckets": buckets}
