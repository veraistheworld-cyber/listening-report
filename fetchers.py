"""Fetch public playlists without custom signatures or client-secret spoofing."""

from __future__ import annotations

import json
import re
import ssl
from datetime import datetime
from html import unescape
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener, HTTPSHandler

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

# Only follow requests/redirects to these platforms' public hosts.
_ALLOWED_SUFFIXES = (
    "music.163.com",
    "163.com",
    "126.net",
    "163cn.tv",
    "163.lu",
    "y.qq.com",
    "qq.com",
    "gtimg.cn",
    "douyin.com",
    "qishui.com",
    "byteimg.com",
    "douyinpic.com",
)

NETEASE_SONG_BATCH = 200
QQ_PAGE_SIZE = 1000
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
REQUEST_TIMEOUT = 20


class FetchError(Exception):
    def __init__(self, message: str, http_status: int = 400):
        super().__init__(message)
        self.message = message
        self.http_status = http_status


def host_allowed(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower().split(":")[0].rstrip(".")
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return False
    return any(host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_SUFFIXES)


class _AllowlistRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urlparse(newurl).hostname
        if not host_allowed(host):
            raise FetchError(f"短链跳转到了未允许的域名（{host}），已中止请求。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = build_opener(
    _AllowlistRedirects(),
    HTTPSHandler(context=ssl.create_default_context()),
)


def http_get(url: str, *, referer: str | None = None, accept: str | None = None) -> tuple[str, bytes, str]:
    """Return (final_url, raw_bytes, content_type)."""
    host = urlparse(url).hostname
    if not host_allowed(host):
        raise FetchError("只接受网易云、QQ 音乐或汽水分享链接。")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept or "application/json, text/html, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        headers["Referer"] = referer
    req = Request(url, headers=headers, method="GET")
    try:
        with _OPENER.open(req, timeout=REQUEST_TIMEOUT) as resp:
            data = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise FetchError("对方页面或接口返回过大，已中止。")
            final = resp.geturl()
            ctype = resp.headers.get("Content-Type") or ""
            return final, data, ctype
    except FetchError:
        raise
    except HTTPError as exc:
        raise FetchError(f"请求失败（HTTP {exc.code}）。") from exc
    except URLError as exc:
        raise FetchError(f"网络请求失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise FetchError("请求超时，请稍后重试。") from exc


def http_get_json(url: str, *, referer: str | None = None) -> Any:
    final, raw, ctype = http_get(url, referer=referer, accept="application/json, text/plain, */*")
    text = raw.decode("utf-8", errors="replace").strip()
    if text.startswith("callback(") or text.startswith("("):
        # rare jsonp wrapping
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise FetchError("接口返回的不是有效 JSON，暂时无法解析。") from exc


def extract_first_url(text: str) -> str | None:
    m = re.search(r"https?://[^\s<>\"']+", text, re.I)
    return m.group(0).rstrip(").,]，。；、\"'") if m else None


def detect_platform(url: str) -> str | None:
    raw = url.strip()
    if not raw:
        return None
    lowered = raw.lower()
    # Include hash/path even if not a full URL
    blob = lowered
    if "kugou.com" in blob or "kugou.cn" in blob:
        return "kugou"
    if any(
        s in blob
        for s in (
            "music.163.com",
            "y.music.163.com",
            "163cn.tv",
            "163.lu",
        )
    ):
        return "netease"
    if any(
        s in blob
        for s in (
            "y.qq.com",
            "i.y.qq.com",
            "i2.y.qq.com",
            "c.y.qq.com",
            "c6.y.qq.com",
        )
    ) or re.search(r"(?:^|//)(?:[a-z0-9-]+\.)*qq\.com/.+", lowered):
        # Avoid treating random qq.com pages (news, etc.) as playlists unless path looks like music.
        if "y.qq.com" in blob or "qq.com" in blob and any(
            k in blob for k in ("playlist", "taoge", "songlist", "disstid", "playsquare")
        ):
            return "qq"
        if any(s in blob for s in ("y.qq.com", "c6.y.qq.com", "i.y.qq.com", "i2.y.qq.com")):
            return "qq"
    if any(s in blob for s in ("qishui.douyin.com", "music.douyin.com/qishui", "qishui.com")):
        return "qishui"
    return None


def _query_param(url: str, *names: str) -> str | None:
    parsed = urlparse(url)
    candidates = []
    if parsed.query:
        candidates.append(parsed.query)
    if parsed.fragment:
        # music.163.com/#/playlist?id=xxx
        frag = parsed.fragment
        if "?" in frag:
            candidates.append(frag.split("?", 1)[1])
        elif frag.startswith("id="):
            candidates.append(frag)
    for q in candidates:
        qs = parse_qs(q, keep_blank_values=False)
        for name in names:
            if name in qs and qs[name][0]:
                return qs[name][0]
    return None


# ---------- track helpers ----------

def _track(name: str, artists, album=None, duration_ms=None, year=None, genre_id=None, language=None, genres=None) -> dict:
    if isinstance(artists, str):
        artist_list = [a.strip() for a in re.split(r"[,/、&]|feat\.?", artists) if a.strip()]
    else:
        artist_list = [str(a).strip() for a in (artists or []) if str(a).strip()]
    item = {"name": (name or "").strip() or "未知曲目", "artists": artist_list}
    if album:
        item["album"] = str(album).strip()
    if duration_ms is not None:
        try:
            ms = int(duration_ms)
            if ms > 0:
                item["durationMs"] = ms
        except (TypeError, ValueError):
            pass
    year_val = extract_year(year)
    if year_val is not None:
        item["year"] = year_val
    if genre_id is not None and genre_id is not False and genre_id != "":
        try:
            item["genreId"] = int(genre_id)
        except (TypeError, ValueError):
            pass
    if language is not None and language is not False and language != "":
        if isinstance(language, bool):
            pass
        elif isinstance(language, (int, float)):
            try:
                item["languageId"] = int(language)
            except (TypeError, ValueError):
                pass
        else:
            label = str(language).strip()
            if label:
                item["language"] = label
    if genres:
        if isinstance(genres, str):
            labels = [genres.strip()] if genres.strip() else []
        elif isinstance(genres, (list, tuple)):
            labels = [str(g).strip() for g in genres if str(g).strip()]
        else:
            labels = []
        if labels:
            item["genres"] = labels
    return item


_YEAR_MIN, _YEAR_MAX = 1900, 2026


def extract_year(*values: Any) -> int | None:
    """Return a plausible release year from already-fetched fields, or None.

    Accepts a calendar year (1900-2026), YYYY / YYYY-MM-DD strings,
    YYYYMMDD integers, or unix timestamps in seconds or milliseconds.
    Does not guess or invent a year when the value is missing or implausible.
    """
    for value in values:
        year = _coerce_year(value)
        if year is not None:
            return year
    return None


def _coerce_year(value: Any) -> int | None:
    if value is None or value is False or value == "":
        return None
    if isinstance(value, datetime):
        year = value.year
        return year if _YEAR_MIN <= year <= _YEAR_MAX else None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _year_from_number(int(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        m = re.match(r"^(\d{4})(?:[-/.年](\d{1,2})(?:[-/.月](\d{1,2}))?)?", text)
        if m and not re.match(r"^\d{10,}$", text):
            year = int(m.group(1))
            if _YEAR_MIN <= year <= _YEAR_MAX:
                return year
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            try:
                num = int(float(text))
            except ValueError:
                return None
            return _year_from_number(num)
    return None


def _year_from_number(n: int) -> int | None:
    if _YEAR_MIN <= n <= _YEAR_MAX:
        return n
    # Compact date like 20180520
    if 19000101 <= n <= 20261231:
        year, month_day = divmod(n, 10000)
        month, day = divmod(month_day, 100)
        if 1 <= month <= 12 and 1 <= day <= 31 and _YEAR_MIN <= year <= _YEAR_MAX:
            return year
        return None
    if n <= 0:
        return None
    try:
        if n >= 10**12:  # milliseconds
            year = datetime.fromtimestamp(n / 1000.0).year
        elif n >= 10**9:  # seconds
            year = datetime.fromtimestamp(float(n)).year
        else:
            return None
    except (OverflowError, OSError, ValueError):
        return None
    if _YEAR_MIN <= year <= _YEAR_MAX:
        return year
    return None


def _payload(platform: str, title: str, tracks: list[dict], *, warning: str | None = None, source_id: str | None = None) -> dict:
    data = {
        "platform": platform,
        "title": title or "未命名歌单",
        "trackCount": len(tracks),
        "tracks": tracks,
    }
    if warning:
        data["warning"] = warning
    if source_id:
        data["sourceId"] = source_id
    return data


# ---------- NetEase ----------

def parse_netease_id(url: str) -> str | None:
    text = unquote(url.strip())
    # Common forms: #/playlist?id=, /playlist?id=, /m/playlist?id=, /playlist/12345
    qid = _query_param(text, "id")
    if qid and qid.isdigit():
        # Avoid picking up other ids when both song and playlist exist; prefer playlist context.
        if "playlist" in text.lower() or "songlist" in text.lower() or re.search(r"[#/](?:my/m/music/)?playlist", text, re.I):
            return qid
        if re.search(r"music\.163\.com|163cn\.tv|163\.lu", text, re.I) and "song?" not in text.lower() and "/song?" not in text.lower() and "/song/" not in text.lower():
            return qid
        if "playlist" in text.lower():
            return qid
    m = re.search(r"/playlist/(\d+)", text)
    if m:
        return m.group(1)
    if qid and qid.isdigit() and "playlist" in text.lower():
        return qid
    return qid if qid and qid.isdigit() and "playlist" in text.lower() else None


def _resolve_netease_url(url: str) -> str:
    if re.search(r"163cn\.tv|163\.lu", url, re.I) or "link" in urlparse(url).path.lower():
        final, _, _ = http_get(url, referer="https://music.163.com/")
        return final
    return url


def fetch_netease(url: str) -> dict:
    resolved = _resolve_netease_url(url)
    pid = parse_netease_id(resolved) or parse_netease_id(url)
    if not pid:
        # Short links may land on an HTML page that still contains the id.
        if resolved.startswith("http"):
            try:
                _, raw, _ = http_get(resolved, referer="https://music.163.com/")
                html = raw.decode("utf-8", errors="ignore")
                m = re.search(r"playlist\?id=(\d+)", html) or re.search(r"playlist/(\d+)", html)
                if m:
                    pid = m.group(1)
            except FetchError:
                pass
    if not pid:
        raise FetchError("没能从链接里解析出网易云歌单 ID。请使用公开歌单链接，或改用「歌名 - 歌手」文本。")

    detail_url = f"https://music.163.com/api/v6/playlist/detail?id={pid}&n=1000"
    data = http_get_json(detail_url, referer="https://music.163.com/")
    code = data.get("code")
    if code in (20001, 401, 403):
        raise FetchError("无法获取该歌单：可能是私密歌单，或需要登录后才能查看。")
    if code in (404, 400):
        raise FetchError("找不到这首单：链接可能已失效，或歌单已被删除。")
    if code != 200:
        raise FetchError(f"网易云返回异常（code={code}），暂时无法拉取。")

    playlist = data.get("playlist") or {}
    if not playlist:
        raise FetchError("无法获取该歌单：可能是私密歌单，或需要登录后才能查看。")

    title = playlist.get("name") or "未命名歌单"
    privacy = playlist.get("privacy") or 0
    declared = playlist.get("trackCount") or 0
    raw_tracks = playlist.get("tracks") or []
    track_ids = []
    for item in playlist.get("trackIds") or []:
        if isinstance(item, dict):
            track_ids.append(item.get("id"))
        else:
            track_ids.append(item)
    track_ids = [i for i in track_ids if i is not None]

    if privacy and not raw_tracks and not track_ids:
        raise FetchError("无法获取该歌单：可能是私密歌单，或需要登录后才能查看。")

    tracks: list[dict] = []
    if raw_tracks and (not track_ids or len(raw_tracks) >= len(track_ids) or len(raw_tracks) >= declared):
        tracks = [_netease_song_to_track(s) for s in raw_tracks]
    else:
        if not track_ids and raw_tracks:
            tracks = [_netease_song_to_track(s) for s in raw_tracks]
        elif not track_ids:
            raise FetchError("歌单是空的，或没有公开曲目可列出。")
        else:
            tracks = _netease_songs_by_ids(track_ids)

    warning = None
    if declared and len(tracks) < declared:
        warning = f"接口声明共 {declared} 首，实际取到 {len(tracks)} 首（部分可能已下架）。"
    return _payload("netease", title, tracks, warning=warning, source_id=str(pid))


def _netease_song_to_track(song: dict) -> dict:
    artists = song.get("ar") or song.get("artists") or []
    names = []
    for a in artists:
        if isinstance(a, dict):
            names.append(a.get("name") or "")
        else:
            names.append(str(a))
    album = song.get("al") or song.get("album") or {}
    album_obj = album if isinstance(album, dict) else {}
    album_name = album_obj.get("name") if isinstance(album, dict) else album
    duration = song.get("dt") if song.get("dt") is not None else song.get("duration")
    year = extract_year(
        song.get("publishTime"),
        song.get("publish_time"),
        album_obj.get("publishTime"),
        album_obj.get("publish_time"),
        album_obj.get("year"),
    )
    return _track(song.get("name") or "", names, album_name, duration, year)


def _netease_songs_by_ids(ids: list) -> list[dict]:
    by_id: dict[Any, dict] = {}
    for start in range(0, len(ids), NETEASE_SONG_BATCH):
        chunk = ids[start : start + NETEASE_SONG_BATCH]
        id_list = "[" + ",".join(str(i) for i in chunk) + "]"
        from urllib.parse import urlencode

        url = "https://music.163.com/api/song/detail?" + urlencode({"ids": id_list})
        data = http_get_json(url, referer="https://music.163.com/")
        if data.get("code") not in (200, None):
            raise FetchError(f"拉取曲目详情失败（code={data.get('code')}）。")
        for song in data.get("songs") or []:
            by_id[song.get("id")] = song
    tracks = []
    for sid in ids:
        song = by_id.get(sid)
        if song:
            tracks.append(_netease_song_to_track(song))
        else:
            tracks.append(_track("（无法获取的曲目）", []))
    return tracks


# ---------- QQ Music ----------

def parse_qq_id(url: str) -> str | None:
    text = unquote(url.strip())
    for key in ("disstid", "id", "tid"):
        val = _query_param(text, key)
        if val and re.fullmatch(r"\d+", val):
            # Ignore obvious non-playlist pages when possible
            if key == "id" and any(x in text.lower() for x in ("/song/", "songid=", "songmid=")) and "playlist" not in text.lower() and "taoge" not in text.lower():
                continue
            return val
    m = re.search(r"/playlist/(\d+)", text)
    if m:
        return m.group(1)
    m = re.search(r"(?:taoge|playsquare|playlist)[^\d]{0,20}(\d{5,})", text, re.I)
    if m:
        return m.group(1)
    return None


def _resolve_qq_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    # Short links and share pages often need a hop before the numeric id is visible.
    if any(s in host for s in ("c6.y.qq.com", "url.cn")) or "/u/" in url or "share" in url.lower():
        final, raw, _ = http_get(url, referer="https://y.qq.com/")
        pid = parse_qq_id(final)
        if not pid:
            html = raw.decode("utf-8", errors="ignore")
            m = re.search(r"/playlist/(\d+)", html) or re.search(r"disstid['\"=:]+(\d+)", html, re.I)
            if m:
                return "https://y.qq.com/n/ryqq/playlist/" + m.group(1)
        return final
    return url


def fetch_qq(url: str) -> dict:
    resolved = _resolve_qq_url(url)
    pid = parse_qq_id(resolved) or parse_qq_id(url)
    if not pid:
        raise FetchError("没能从链接里解析出 QQ 音乐歌单 ID。请使用公开歌单链接，或改用「歌名 - 歌手」文本。")

    def page(begin: int, num: int) -> dict:
        q = (
            "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
            f"?type=1&json=1&utf8=1&onlysong=0&new_format=1&format=json"
            f"&disstid={pid}&song_begin={begin}&song_num={num}"
        )
        return http_get_json(q, referer="https://y.qq.com/")

    first = page(0, QQ_PAGE_SIZE)
    code = first.get("code")
    cdlist = first.get("cdlist") or []
    if code not in (0, "0") or not cdlist:
        # Retry without pagination params; some responses omit songlist when begin/num are present.
        q = (
            "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"
            f"?type=1&json=1&utf8=1&onlysong=0&new_format=1&format=json&disstid={pid}"
        )
        first = http_get_json(q, referer="https://y.qq.com/")
        code = first.get("code")
        cdlist = first.get("cdlist") or []
    if code not in (0, "0") or not cdlist:
        raise FetchError("找不到这首单，或这是私密/受限的 QQ 歌单，无法公开拉取。")

    cd = cdlist[0]
    title = cd.get("dissname") or "未命名歌单"
    declared = int(cd.get("songnum") or 0)
    songs = list(cd.get("songlist") or [])

    # Paginate if the first response was truncated.
    begin = len(songs)
    guard = 0
    while declared and len(songs) < declared and guard < 20:
        more = page(begin, QQ_PAGE_SIZE)
        more_list = ((more.get("cdlist") or [{}])[0].get("songlist") or [])
        if not more_list:
            break
        songs.extend(more_list)
        begin = len(songs)
        guard += 1

    tracks = [_qq_song_to_track(s) for s in songs]
    warning = None
    if declared and len(tracks) < declared:
        warning = f"接口声明共 {declared} 首，实际取到 {len(tracks)} 首。"
    return _payload("qq", title, tracks, warning=warning, source_id=str(pid))


def _qq_song_to_track(song: dict) -> dict:
    name = song.get("name") or song.get("songname") or song.get("title") or ""
    singers = song.get("singer") or []
    names = []
    if isinstance(singers, str):
        names = [singers]
    else:
        for s in singers:
            if isinstance(s, dict):
                names.append(s.get("name") or s.get("title") or "")
            else:
                names.append(str(s))
    if not names and song.get("singername"):
        names = [song["singername"]]
    album = song.get("album") or {}
    if isinstance(album, dict):
        album_name = album.get("name") or album.get("title")
    else:
        album_name = album or song.get("albumname")
    interval = song.get("interval")
    duration_ms = int(interval) * 1000 if interval not in (None, "") else song.get("duration")
    album_obj = album if isinstance(album, dict) else {}
    year = extract_year(
        song.get("time_public"),
        song.get("publicTime"),
        song.get("pub_time"),
        song.get("public_time"),
        song.get("year"),
        album_obj.get("time_public"),
        album_obj.get("publicTime"),
        album_obj.get("pub_time"),
        album_obj.get("public_time"),
        album_obj.get("year"),
    )
    genre_id = song.get("genre")
    language = song.get("language")
    return _track(name, names, album_name, duration_ms, year, genre_id, language)


# ---------- Qishui (beta): public share-page embedded data only ----------

def fetch_qishui(url: str) -> dict:
    # Follow short links like https://qishui.douyin.com/s/xxxxx/
    final, raw, _ = http_get(url, referer="https://music.douyin.com/")
    parsed = urlparse(final)
    path = parsed.path.lower()
    if "/share/track" in path or "track_id=" in (parsed.query + final):
        # A short link may resolve to a single track, not a playlist.
        if "playlist_id=" not in final and "/share/playlist" not in path:
            raise FetchError("这是汽水单曲分享链接，不是歌单。请贴歌单分享链接，或改用「歌名 - 歌手」文本。")

    html = raw.decode("utf-8", errors="replace")
    # If the first hop didn't include embedded data (e.g. intermediate page), refetch final.
    if "_ROUTER_DATA" not in html and "/share/playlist" in path:
        final, raw, _ = http_get(final, referer="https://music.douyin.com/")
        html = raw.decode("utf-8", errors="replace")

    page = _extract_qishui_router(html)
    if not page:
        raise FetchError(
            "未能从汽水分享页解析出歌单（汽水支持为 beta）。"
            "请改用「歌名 - 歌手」文本，或先转到网易云 / QQ 后再拉取。"
        )

    info = page.get("playlistInfo") or {}
    title = info.get("title") or info.get("public_title") or "未命名歌单"
    declared = info.get("count_tracks") or 0
    resource = info.get("resource_cnt") or {}
    if isinstance(resource, dict) and resource.get("track_cnt"):
        # Visible / total count when the share page trims the embedded list.
        declared = max(int(declared or 0), int(resource.get("track_cnt") or 0))

    tracks = []
    for media in page.get("medias") or []:
        track = ((media.get("entity") or {}).get("track")) or {}
        if not track:
            continue
        artists = [a.get("name") for a in (track.get("artists") or []) if isinstance(a, dict) and a.get("name")]
        album_obj = track.get("album") if isinstance(track.get("album"), dict) else {}
        album = album_obj.get("name") if album_obj else None
        year = extract_year(
            track.get("year"),
            track.get("release_date"),
            track.get("releaseDate"),
            track.get("publish_time"),
            track.get("publishTime"),
            album_obj.get("year"),
            album_obj.get("release_date"),
            album_obj.get("releaseDate"),
            album_obj.get("publish_time"),
            album_obj.get("publishTime"),
        )
        qs_language = None
        for key in ("language", "lang", "language_name"):
            val = track.get(key)
            if isinstance(val, str) and val.strip():
                qs_language = val.strip()
                break
        qs_genres = None
        raw_tags = track.get("genres") or track.get("tags") or track.get("tag_list")
        if raw_tags is None and isinstance(track.get("genre"), str):
            raw_tags = track.get("genre")
        if isinstance(raw_tags, str) and raw_tags.strip():
            qs_genres = [raw_tags.strip()]
        elif isinstance(raw_tags, list):
            labels = []
            for item in raw_tags:
                if isinstance(item, dict):
                    label = item.get("name") or item.get("title") or item.get("tag")
                else:
                    label = item
                if isinstance(label, str) and label.strip():
                    labels.append(label.strip())
            if labels:
                qs_genres = labels
        tracks.append(_track(track.get("name") or "", artists, album, track.get("duration"), year, None, qs_language, qs_genres))

    if not tracks:
        raise FetchError("汽水歌单里没有解析到曲目（可能是私密或分享页已失效）。可改用文本粘贴。")

    warning = None
    if declared and len(tracks) < declared:
        warning = (
            f"分享页仅嵌入了 {len(tracks)}/{declared} 首，已列出当前可见曲目。"
            "汽水支持为 beta，完整列表请改用「歌名 - 歌手」文本。"
        )
    elif not warning:
        warning = None
    payload = _payload("qishui", title, tracks, warning=warning, source_id=str(info.get("id") or ""))
    payload["beta"] = True
    return payload


def _extract_qishui_router(html: str) -> dict | None:
    idx = html.find("_ROUTER_DATA = ")
    if idx < 0:
        idx = html.find("_ROUTER_DATA=")
        token = "_ROUTER_DATA="
    else:
        token = "_ROUTER_DATA = "
    if idx < 0:
        return None
    rest = html[idx + len(token) :].lstrip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(rest)
    except json.JSONDecodeError:
        return None
    loader = obj.get("loaderData") or {}
    page = loader.get("playlist_page") or loader.get("playlist") or {}
    if not isinstance(page, dict):
        return None
    return page


# ---------- Manual text fallback ----------

_TEXT_LINE = re.compile(
    r"""
    ^\s*
    (?:(?:\d+)[\.、\)\]]\s*)?      # optional leading index
    (.+?)
    \s*[-–—－|/｜]\s*
    (.+?)
    \s*$
    """,
    re.VERBOSE,
)


def parse_text_tracks(text: str, title: str | None = None) -> dict:
    lines = [unescape(ln.strip()) for ln in text.splitlines()]
    tracks = []
    skipped = 0
    for line in lines:
        if not line or line.startswith("#"):
            continue
        # Ignore a bare URL line in the text area.
        if re.match(r"https?://", line, re.I) and " - " not in line:
            skipped += 1
            continue
        m = _TEXT_LINE.match(line)
        if not m:
            # "歌名  歌手" with 2+ spaces or a tab
            m2 = re.match(r"^(.+?)(?:\t| {2,})(.+)$", line)
            if m2:
                name, artists = m2.group(1), m2.group(2)
            else:
                skipped += 1
                continue
        else:
            name, artists = m.group(1), m.group(2)
        tracks.append(_track(name, artists))
    if not tracks:
        raise FetchError(
            "没有解析到「歌名 - 歌手」格式的曲目。每行一首，例如：海阔天空 - Beyond"
        )
    warning = None
    if skipped:
        warning = f"有 {skipped} 行无法识别，已跳过。"
    return _payload("text", title or "手动粘贴的歌单", tracks, warning=warning)


# ---------- kugou / dispatcher ----------

KUGOU_HINT = (
    "暂不支持酷狗链接直拉。请改用「歌名 - 歌手」文本粘贴，"
    "或先到 https://music.unmeta.cn/ 将歌单转到网易云 / QQ / 文本后再来。"
)


def fetch_playlist(url: str | None = None, text: str | None = None) -> dict:
    url = (url or "").strip()
    text = (text or "").strip()

    candidate = url or ""
    extracted = extract_first_url(url) or extract_first_url(text)
    if extracted:
        candidate = extracted

    platform = detect_platform(candidate) if candidate else None

    if platform == "kugou":
        raise FetchError(KUGOU_HINT)

    if platform == "netease":
        return fetch_netease(candidate)
    if platform == "qq":
        return fetch_qq(candidate)
    if platform == "qishui":
        return fetch_qishui(candidate)

    # No recognized link: treat as manual text (prefer explicit text, else the url field).
    blob = text or url
    if blob and not re.search(r"https?://", blob):
        return parse_text_tracks(blob)
    if blob and extract_first_url(blob) and not platform:
        raise FetchError(
            "无法识别这个链接所属平台。目前支持网易云、QQ 音乐公开歌单直拉，"
            "汽水（beta）分享页，以及「歌名 - 歌手」文本。酷狗请先转换或改贴文本。"
        )
    if blob:
        # Mixed paste without a recognized URL — try text parser.
        return parse_text_tracks(blob)
    raise FetchError("请粘贴歌单链接，或按「歌名 - 歌手」每行一首粘贴曲目文本。")
