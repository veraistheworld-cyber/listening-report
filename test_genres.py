#!/usr/bin/env python3
"""Stdlib tests for unified genre mapping, matching, cache, and stats."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from analyze import analyze_playlist
from fetchers import _track
from genres import (
    QQ_GENRE_ID_TO_LABEL,
    artists_match,
    cache_key,
    enrich_tracks,
    genres_from_track,
    language_from_track,
    map_qq_genre_id,
    map_qq_language_id,
    map_tag,
    map_tags,
    pick_strict_qq_match,
    titles_match,
)


class TestQQGenreMapping(unittest.TestCase):
    def test_verified_ids(self):
        self.assertEqual(map_qq_genre_id(1), "流行")
        self.assertEqual(map_qq_genre_id("1"), "流行")
        self.assertEqual(map_qq_genre_id(34), "说唱")
        self.assertEqual(map_qq_genre_id(36), "摇滚")
        self.assertEqual(map_qq_genre_id(20), "舞曲")
        self.assertEqual(map_qq_genre_id(22), "电子")
        self.assertEqual(map_qq_genre_id(23), "民谣")
        self.assertEqual(map_qq_genre_id(19), "乡村")
        self.assertEqual(map_qq_genre_id(37), "原声/影视")
        self.assertEqual(map_qq_genre_id(33), "R&B")
        self.assertEqual(map_qq_genre_id(15), "蓝调")
        self.assertEqual(map_qq_genre_id(35), "雷鬼")

    def test_unknown_id_is_not_pop(self):
        for gid in (0, 2, 3, 27, 28, 39, 41, 50, 51, 58, 70, 999, None, "", False, "foo"):
            self.assertIsNone(map_qq_genre_id(gid), msg=f"id={gid!r} should stay unmapped")
        self.assertNotIn(0, QQ_GENRE_ID_TO_LABEL)
        self.assertNotIn(50, QQ_GENRE_ID_TO_LABEL)
        self.assertNotIn(51, QQ_GENRE_ID_TO_LABEL)

    def test_language_ids(self):
        self.assertEqual(map_qq_language_id(0), "国语")
        self.assertEqual(map_qq_language_id(1), "粤语")
        self.assertEqual(map_qq_language_id(2), "闽南语")
        self.assertEqual(map_qq_language_id(3), "日语")
        self.assertEqual(map_qq_language_id(4), "韩语")
        self.assertEqual(map_qq_language_id(5), "英语")
        self.assertEqual(map_qq_language_id(9), "纯音乐")
        self.assertEqual(map_qq_language_id(16), "西班牙语")
        self.assertIsNone(map_qq_language_id(7))
        self.assertIsNone(map_qq_language_id(None))


class TestTagMapping(unittest.TestCase):
    def test_common_tags(self):
        self.assertEqual(map_tag("Hip-Hop"), "说唱")
        self.assertEqual(map_tag("indie rock"), "独立/另类")
        self.assertEqual(map_tag("Soundtrack"), "原声/影视")
        self.assertEqual(map_tag("国风"), "国风")
        self.assertIsNone(map_tag("seen live"))
        self.assertIsNone(map_tag("female vocalists"))
        self.assertIsNone(map_tag("unknown-micro-genre-xyz"))
        self.assertEqual(map_tags(["pop", "rap", "pop", "loved"]), ["流行", "说唱"])


class TestNormalization(unittest.TestCase):
    def test_titles_match_ignores_version_attrs(self):
        self.assertTrue(titles_match("晴天 (Live)", "晴天"))
        self.assertTrue(titles_match("Lose Yourself (Explicit)", "Lose Yourself"))
        self.assertTrue(titles_match("Hey Jude (Love Version)", "Hey Jude"))
        self.assertTrue(titles_match("成都（现场）", "成都"))
        self.assertTrue(titles_match("Numb (Acoustic)", "Numb"))

    def test_titles_reject_different_songs(self):
        self.assertFalse(titles_match("海阔天空", "光辉岁月"))
        self.assertFalse(titles_match("晴天", "七里香"))
        self.assertFalse(titles_match("Hello", "Hello Goodbye"))
        self.assertFalse(titles_match("", "晴天"))

    def test_artists_match(self):
        self.assertTrue(artists_match(["IU (아이유)"], ["IU"]))
        self.assertTrue(artists_match(["米津玄師"], ["米津玄师"]))
        self.assertTrue(artists_match(["The Beatles"], ["Beatles"]))
        self.assertTrue(artists_match(["周杰伦", "费玉清"], ["周杰伦"]))
        self.assertFalse(artists_match(["周杰伦"], ["林俊杰"]))
        self.assertFalse(artists_match([], ["周杰伦"]))

    def test_strict_qq_pick(self):
        results = [
            {"songname": "Lose Yourself (Markino Faded Remix)", "singer": [{"name": "Dj Markino"}], "songmid": "wrong"},
            {"songname": "Lose Yourself (Explicit)", "singer": [{"name": "Eminem"}], "songmid": "ok"},
        ]
        hit = pick_strict_qq_match("Lose Yourself", ["Eminem"], results)
        self.assertIsNotNone(hit)
        self.assertEqual(hit["songmid"], "ok")
        self.assertIsNone(pick_strict_qq_match("Lose Yourself", ["Eminem"], results[:1]))
        self.assertIsNone(pick_strict_qq_match("晴天", ["周杰伦"], [
            {"songname": "七里香", "singer": [{"name": "周杰伦"}], "songmid": "x"}
        ]))

    def test_cache_key_stable(self):
        self.assertEqual(
            cache_key("晴天 (Live)", ["周杰伦"]),
            cache_key("晴天", ["周杰伦"]),
        )


class TestTrackFields(unittest.TestCase):
    def test_qq_track_helper_keeps_ids(self):
        item = _track("晴天", ["周杰伦"], "叶惠美", 269000, 2003, 1, 0)
        self.assertEqual(item["genreId"], 1)
        self.assertEqual(item["languageId"], 0)
        self.assertNotIn("genres", item)

    def test_missing_fields_do_not_invent(self):
        item = _track("无标签", ["测试"])
        self.assertNotIn("genreId", item)
        self.assertNotIn("languageId", item)
        self.assertNotIn("genres", item)
        self.assertNotIn("language", item)
        self.assertEqual(genres_from_track(item), [])
        self.assertIsNone(language_from_track(item))
        self.assertEqual(genres_from_track({}), [])
        self.assertEqual(genres_from_track({"genreId": 50}), [])
        self.assertEqual(genres_from_track({"genreId": 0}), [])
        self.assertEqual(genres_from_track({"genre": "definitely-not-a-real-tag"}), [])


class TestAnalyzeGenreStats(unittest.TestCase):
    def _tracks(self, specs):
        out = []
        for i, spec in enumerate(specs):
            item = {"name": f"T{i}", "artists": ["A"]}
            item.update(spec)
            out.append(item)
        return out

    def test_multi_label_counts_and_share_uses_total(self):
        tracks = self._tracks(
            [{"genres": ["流行", "说唱"]}] * 6
            + [{"genres": ["流行"]}] * 4
            + [{"genres": ["摇滚"]}] * 2
        )
        # 12 tracks, all identified
        result = analyze_playlist({"title": "多标签", "tracks": tracks}, enrich=False)
        genres = result["stats"]["genres"]
        self.assertIsNotNone(genres)
        self.assertEqual(genres["covered"], 12)
        by_name = {b["name"]: b for b in genres["buckets"]}
        self.assertEqual(by_name["流行"]["count"], 10)
        self.assertEqual(by_name["流行"]["share"], round(10 / 12, 4))
        self.assertEqual(by_name["说唱"]["count"], 6)
        self.assertEqual(by_name["说唱"]["share"], round(6 / 12, 4))
        self.assertEqual(by_name["摇滚"]["count"], 2)
        self.assertTrue(any("基于已识别风格的 12 / 12 首" in n for n in result["notes"]))

    def test_coverage_threshold_too_few(self):
        tracks = self._tracks([{"genreId": 1}] * 7 + [{}] * 13)
        result = analyze_playlist({"title": "不足首数", "tracks": tracks}, enrich=False)
        self.assertIsNone(result["stats"]["genres"])
        self.assertTrue(any("基于已识别风格的 7 / 20 首" in n for n in result["notes"]))
        self.assertTrue(any("不足以展示风格分布" in n for n in result["notes"]))

    def test_coverage_threshold_too_low_ratio(self):
        # 8 identified of 30 = 26.7% < 30%
        tracks = self._tracks([{"genreId": 34}] * 8 + [{}] * 22)
        result = analyze_playlist({"title": "覆盖不足", "tracks": tracks}, enrich=False)
        self.assertIsNone(result["stats"]["genres"])
        self.assertTrue(any("基于已识别风格的 8 / 30 首" in n for n in result["notes"]))
        self.assertTrue(any("不足以展示风格分布" in n for n in result["notes"]))

    def test_coverage_passes_and_maps_qq_ids(self):
        tracks = self._tracks([{"genreId": 1, "languageId": 0}] * 6 + [{"genreId": 34, "languageId": 5}] * 4)
        result = analyze_playlist({"title": "够覆盖", "tracks": tracks}, enrich=False)
        genres = result["stats"]["genres"]
        languages = result["stats"]["languages"]
        self.assertIsNotNone(genres)
        self.assertEqual(genres["covered"], 10)
        by_name = {b["name"]: b for b in genres["buckets"]}
        self.assertEqual(by_name["流行"]["count"], 6)
        self.assertEqual(by_name["流行"]["share"], 0.6)
        self.assertEqual(by_name["说唱"]["count"], 4)
        self.assertIsNotNone(languages)
        lang = {b["name"]: b for b in languages["buckets"]}
        self.assertEqual(lang["国语"]["count"], 6)
        self.assertEqual(lang["英语"]["count"], 4)

    def test_unknown_id_does_not_count_as_pop(self):
        tracks = self._tracks([{"genreId": 50}] * 10)
        result = analyze_playlist({"title": "未知编号", "tracks": tracks}, enrich=False)
        self.assertIsNone(result["stats"]["genres"])
        self.assertTrue(any("没有已识别的风格" in n for n in result["notes"]))

    def test_missing_genre_fields_no_block(self):
        tracks = self._tracks([{}] * 10)
        result = analyze_playlist({"title": "无风格字段", "tracks": tracks}, enrich=False)
        self.assertIsNone(result["stats"]["genres"])
        self.assertIsNone(result["stats"]["languages"])
        self.assertIn("genres", result["stats"])
        self.assertIn("languages", result["stats"])


class TestEnrichCache(unittest.TestCase):
    def test_cache_hit_does_not_request_again(self):
        calls: list[str] = []

        def fake_fetch(url, *, referer=None):
            calls.append(url)
            if "client_search_cp" in url:
                return {
                    "data": {
                        "song": {
                            "list": [
                                {
                                    "songname": "晴天",
                                    "singer": [{"name": "周杰伦"}],
                                    "songmid": "abc",
                                }
                            ]
                        }
                    }
                }
            if "fcg_play_single_song" in url:
                return {"data": [{"name": "晴天", "singer": [{"name": "周杰伦"}], "genre": 1, "language": 0}]}
            raise AssertionError(f"unexpected url {url}")

        tracks = [{"name": "晴天", "artists": ["周杰伦"]}]
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "genre-cache.json"
            meta1 = enrich_tracks(
                tracks,
                cache_path=cache,
                budget_s=10,
                fetch_json=fake_fetch,
                enable_musicbrainz=False,
                lastfm_api_key="",
            )
            self.assertEqual(tracks[0]["genres"], ["流行"])
            self.assertEqual(tracks[0]["language"], "国语")
            self.assertGreaterEqual(len(calls), 2)
            first_calls = list(calls)
            self.assertTrue(cache.is_file())

            tracks2 = [{"name": "晴天 (Live)", "artists": ["周杰伦"]}]
            meta2 = enrich_tracks(
                tracks2,
                cache_path=cache,
                budget_s=10,
                fetch_json=fake_fetch,
                enable_musicbrainz=False,
                lastfm_api_key="",
            )
            self.assertEqual(len(calls), len(first_calls))
            self.assertEqual(tracks2[0]["genres"], ["流行"])
            self.assertGreaterEqual(meta2["cacheHits"], 1)

            saved = json.loads(cache.read_text(encoding="utf-8"))
            self.assertIn(cache_key("晴天", ["周杰伦"]), saved["entries"])

    def test_mismatch_is_cached_as_miss_and_not_guessed(self):
        calls = {"n": 0}

        def fake_fetch(url, *, referer=None):
            calls["n"] += 1
            return {
                "data": {
                    "song": {
                        "list": [
                            {
                                "songname": "七里香",
                                "singer": [{"name": "周杰伦"}],
                                "songmid": "nope",
                            }
                        ]
                    }
                }
            }

        tracks = [{"name": "晴天", "artists": ["周杰伦"]}]
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "genre-cache.json"
            enrich_tracks(
                tracks,
                cache_path=cache,
                budget_s=10,
                fetch_json=fake_fetch,
                enable_musicbrainz=False,
                lastfm_api_key="",
            )
            self.assertNotIn("genres", tracks[0])
            first = calls["n"]
            enrich_tracks(
                [{"name": "晴天", "artists": ["周杰伦"]}],
                cache_path=cache,
                budget_s=10,
                fetch_json=fake_fetch,
                enable_musicbrainz=False,
                lastfm_api_key="",
            )
            self.assertEqual(calls["n"], first)

    def test_analyze_uses_injected_fetcher(self):
        def fake_fetch(url, *, referer=None):
            if "client_search_cp" in url:
                return {
                    "data": {
                        "song": {
                            "list": [
                                {
                                    "songname": "以父之名",
                                    "singer": [{"name": "周杰伦"}],
                                    "songmid": "mid",
                                }
                            ]
                        }
                    }
                }
            return {"data": [{"name": "以父之名", "singer": [{"name": "周杰伦"}], "genre": 34, "language": 0}]}

        tracks = [{"name": "以父之名", "artists": ["周杰伦"]} for _ in range(10)]
        with tempfile.TemporaryDirectory() as tmp:
            result = analyze_playlist(
                {"title": "补全", "tracks": tracks},
                enrich=True,
                cache_path=Path(tmp) / "genre-cache.json",
                budget_s=10,
                fetch_json=fake_fetch,
            )
        genres = result["stats"]["genres"]
        self.assertIsNotNone(genres)
        self.assertEqual(genres["covered"], 10)
        self.assertEqual(genres["buckets"][0]["name"], "说唱")


if __name__ == "__main__":
    unittest.main()
