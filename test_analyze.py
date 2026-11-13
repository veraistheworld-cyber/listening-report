#!/usr/bin/env python3
"""Stdlib tests for the rule-based playlist report engine."""

from __future__ import annotations

import unittest

from analyze import analyze_playlist as _analyze_playlist
from fetchers import FetchError, _track, extract_year


def analyze_playlist(body):
    """Existing stats tests stay offline; they do not exercise enrichment."""
    return _analyze_playlist(body, enrich=False)


def _song(name, artists, album=None, duration_ms=None, year=None):
    item = {"name": name, "artists": artists}
    if album is not None:
        item["album"] = album
    if duration_ms is not None:
        item["durationMs"] = duration_ms
    if year is not None:
        item["year"] = year
    return item


SAMPLE_TRACKS = [
    _song("Song A", ["Alice"], "Album X", 180000, 2020),
    _song("Song B", ["Alice"], "Album X", 200000, 2021),
    _song("Song C", ["Alice", "Bob"], "Album Y", 240000, 2018),
    _song("Song D", ["Bob"], "Album Y", 120000, 2015),
    _song("Song E", ["Carol"], "Album Z", 300000, 1999),
    _song("Song F", ["Alice"], "Album X", 210000, 2022),
    _song("Song G", ["Bob", "Carol"], "Solo G", None, 2010),
    _song("Song H", ["Dave"], "Solo H", 90000, None),
    _song("Song I", ["Alice"], "Solo I", None, 2023),
    _song("Song J", ["Eve"], "Solo J", 150000, 2005),
]


class TestExtractYear(unittest.TestCase):
    def test_calendar_year(self):
        self.assertEqual(extract_year(2020), 2020)
        self.assertEqual(extract_year("2020"), 2020)
        self.assertEqual(extract_year("2019-08-16"), 2019)
        self.assertEqual(extract_year("2019/08/16"), 2019)
        self.assertEqual(extract_year("2018年5月"), 2018)
        self.assertEqual(extract_year(20180520), 2018)

    def test_timestamps(self):
        # 2021-01-01 00:00:00 UTC = 2021-01-01 08:00 Asia/Shanghai
        self.assertEqual(extract_year(1609459200000), 2021)
        self.assertEqual(extract_year(1609459200), 2021)
        self.assertEqual(extract_year("1609459200000"), 2021)

    def test_rejects_implausible(self):
        self.assertIsNone(extract_year(None))
        self.assertIsNone(extract_year(""))
        self.assertIsNone(extract_year(0))
        self.assertIsNone(extract_year(176766))
        self.assertIsNone(extract_year(1899))
        self.assertIsNone(extract_year(2027))
        self.assertIsNone(extract_year(False))

    def test_first_valid_wins(self):
        self.assertEqual(extract_year(None, 0, "2016-03-01", 2020), 2016)

    def test_track_helper_writes_year(self):
        item = _track("夜曲", ["周杰伦"], "十一月的萧邦", 226000, "2005-11-01")
        self.assertEqual(item["year"], 2005)
        bare = _track("无年份", ["测试"])
        self.assertNotIn("year", bare)


class TestAnalyzePlaylist(unittest.TestCase):
    def test_missing_tracks_raises(self):
        with self.assertRaises(FetchError) as ctx:
            analyze_playlist({"title": "空"})
        self.assertIn("tracks", ctx.exception.message)
        with self.assertRaises(FetchError):
            analyze_playlist({"tracks": "不是列表"})

    def test_full_stats(self):
        result = analyze_playlist({
            "title": "测试歌单",
            "platform": "text",
            "trackCount": 10,
            "tracks": SAMPLE_TRACKS,
        })
        self.assertTrue(result["ok"])
        self.assertEqual(result["title"], "测试歌单")
        self.assertEqual(result["platform"], "text")
        self.assertEqual(result["trackCount"], 10)
        stats = result["stats"]
        self.assertEqual(stats["trackCount"], 10)
        self.assertEqual(stats["uniqueArtists"], 5)
        self.assertEqual(stats["uniqueAlbums"], 7)

        top = stats["topArtists"]
        self.assertEqual([a["name"] for a in top[:3]], ["Alice", "Bob", "Carol"])
        self.assertEqual(top[0]["count"], 5)
        self.assertEqual(top[0]["share"], 0.5)
        self.assertEqual(top[1]["count"], 3)
        self.assertEqual(top[1]["share"], 0.3)
        self.assertEqual(top[2]["count"], 2)
        self.assertEqual(top[2]["share"], 0.2)

        # Union of tracks featuring any of the top 3: 8 of 10 (Dave, Eve excluded)
        self.assertEqual(stats["top3Share"], 0.8)
        self.assertEqual(stats["collabRate"], 0.2)

        duration = stats["duration"]
        self.assertIsNotNone(duration)
        self.assertEqual(duration["covered"], 8)
        self.assertEqual(duration["totalMs"], 1490000)
        self.assertEqual(duration["avgMs"], 186250)
        self.assertEqual(duration["longest"]["name"], "Song E")
        self.assertEqual(duration["longest"]["artists"], ["Carol"])
        self.assertEqual(duration["longest"]["durationMs"], 300000)
        self.assertEqual(duration["shortest"]["name"], "Song H")
        self.assertEqual(duration["shortest"]["durationMs"], 90000)

        eras = stats["eras"]
        self.assertIsNotNone(eras)
        self.assertEqual(eras["covered"], 9)
        by_label = {b["label"]: b for b in eras["buckets"]}
        self.assertEqual(by_label["1990s"]["count"], 1)
        self.assertEqual(by_label["2000s"]["count"], 1)
        self.assertEqual(by_label["2010s"]["count"], 3)
        self.assertEqual(by_label["2020s"]["count"], 4)
        self.assertEqual(by_label["2020s"]["share"], round(4 / 9, 4))

        albums = {a["name"]: a for a in stats["topAlbums"]}
        self.assertEqual(albums["Album X"]["count"], 3)
        self.assertEqual(albums["Album Y"]["count"], 2)
        self.assertNotIn("Album Z", albums)

        self.assertTrue(result["personality"]["locked"])
        self.assertIn("不调用模型", result["personality"]["teaser"])
        self.assertTrue(any("时长统计仅基于有时长的 8 首" in n for n in result["notes"]))
        self.assertTrue(any("年代分布基于有发行年份的 9 首" in n for n in result["notes"]))

    def test_no_duration_omits_block(self):
        tracks = [
            _song(f"T{i}", ["A" if i < 6 else "B"], year=2010 + (i % 10))
            for i in range(10)
        ]
        result = analyze_playlist({"title": "无时长", "tracks": tracks})
        self.assertIsNone(result["stats"]["duration"])
        self.assertTrue(any("没有时长" in n for n in result["notes"]))
        # No invented duration fields
        self.assertNotIn("totalMs", result["stats"])

    def test_no_year_omits_eras(self):
        tracks = [_song(f"T{i}", ["A"], duration_ms=120000) for i in range(12)]
        result = analyze_playlist({"title": "无年份", "tracks": tracks})
        self.assertIsNone(result["stats"]["eras"])
        self.assertTrue(any("发行年份" in n and "省略" in n for n in result["notes"]))
        self.assertFalse(any(t.get("year") for t in tracks))

    def test_insufficient_year_coverage(self):
        tracks = [_song(f"T{i}", ["A"], duration_ms=120000) for i in range(20)]
        tracks[0]["year"] = 1998
        tracks[1]["year"] = 2021
        result = analyze_playlist({"title": "年份不足", "tracks": tracks})
        self.assertIsNone(result["stats"]["eras"])
        self.assertTrue(any("不足以展示年代分布" in n for n in result["notes"]))

    def test_does_not_invent_year_from_other_fields(self):
        tracks = [
            {"name": "X", "artists": ["A"], "durationMs": 200000, "publishTime": 1609459200000}
            for _ in range(10)
        ]
        result = analyze_playlist({"title": "只有别的字段", "tracks": tracks})
        self.assertIsNone(result["stats"]["eras"])

    def test_invalid_year_ignored(self):
        tracks = [_song(f"T{i}", ["A"], duration_ms=100000, year=1899) for i in range(10)]
        tracks[0]["year"] = 3000
        result = analyze_playlist({"title": "非法年份", "tracks": tracks})
        self.assertIsNone(result["stats"]["eras"])

    def test_empty_artist_names_skipped(self):
        tracks = [
            _song("One", ["", "  ", "Ada"], duration_ms=100000, year=2011),
            _song("Two", ["Ada"], duration_ms=110000, year=2012),
            _song("Three", ["Bea"], duration_ms=120000, year=2013),
            _song("Four", ["Ada", "Bea"], duration_ms=130000, year=2014),
            _song("Five", ["Cara"], duration_ms=140000, year=2015),
            _song("Six", ["Ada"], duration_ms=150000, year=2016),
            _song("Seven", ["Bea"], duration_ms=160000, year=2017),
            _song("Eight", ["Cara"], duration_ms=170000, year=2018),
        ]
        result = analyze_playlist({"title": "空名", "tracks": tracks})
        names = [a["name"] for a in result["stats"]["topArtists"]]
        self.assertNotIn("", names)
        self.assertEqual(result["stats"]["uniqueArtists"], 3)
        # One + Four are collabs after cleaning empties? Four is Ada/Bea; One only has Ada
        self.assertEqual(result["stats"]["collabRate"], 0.125)

    def test_zero_tracks(self):
        result = analyze_playlist({"title": "空列表", "platform": "text", "tracks": []})
        self.assertEqual(result["stats"]["trackCount"], 0)
        self.assertEqual(result["stats"]["topArtists"], [])
        self.assertEqual(result["stats"]["top3Share"], 0)
        self.assertEqual(result["stats"]["collabRate"], 0)
        self.assertIsNone(result["stats"]["duration"])
        self.assertIsNone(result["stats"]["eras"])


if __name__ == "__main__":
    unittest.main()
