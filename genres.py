"""Unified genre taxonomy, mappings, and optional enrichment.

Python 3.10+ stdlib only. Never guess a genre: unknown IDs, failed
matches, and missing fields stay unidentified.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Canonical labels (top-level only; do not surface platform-raw categories)
# ---------------------------------------------------------------------------

CANONICAL_GENRES: tuple[str, ...] = (
    "流行",
    "说唱",
    "R&B",
    "摇滚",
    "民谣",
    "电子",
    "舞曲",
    "国风",
    "爵士",
    "蓝调",
    "金属",
    "朋克",
    "独立/另类",
    "轻音乐",
    "古典",
    "原声/影视",
    "ACG",
    "乡村",
    "雷鬼",
    "拉丁",
    "世界音乐",
)

CANONICAL_LANGUAGES: tuple[str, ...] = (
    "国语",
    "粤语",
    "闽南语",
    "英语",
    "日语",
    "韩语",
    "西班牙语",
    "纯音乐",
)

_GENRE_SET = frozenset(CANONICAL_GENRES)
_LANGUAGE_SET = frozenset(CANONICAL_LANGUAGES)

# Song-level QQ `genre` IDs, verified 2026-11-12 against public
# c.y.qq.com search + fcg_play_single_song.fcg detail (Referer: y.qq.com).
# These are NOT the singer-filter genre IDs (singer 6=嘻哈, song 34=说唱).
#
# Verified:
#   1  流行     周杰伦《晴天》、陈奕迅《富士山下》、Adele Hello、米津玄师 Lemon
#  15  蓝调     B.B. King《The Thrill Is Gone》、T-Bone Walker《Stormy Monday》、
#              Albert King / Stevie Ray Vaughan《Pride And Joy》
#  19  乡村     John Denver《Take Me Home, Country Roads》、Dolly Parton《Jolene》、
#              Johnny Cash《Ring of Fire》
#  20  舞曲     IU《좋은 날 (Good Day)》、Madonna《Hung Up》、Avicii《Levels》、
#              Deadmau5《Strobe》
#  22  电子     Daft Punk《One More Time》《Harder, Better, Faster, Stronger》、
#              Kraftwerk《Autobahn》
#  23  民谣     赵雷《成都》、宋冬野《董小姐》、Simon & Garfunkel《The Sounds of Silence》
#  33  R&B      陶喆《爱，很简单》、Beyoncé《Crazy In Love》、The Weeknd《Blinding Lights》
#  34  说唱     Eminem《Lose Yourself》、周杰伦《以父之名》、弹壳《怀抱》
#  35  雷鬼     Bob Marley《Is This Love》《Could You Be Loved》、Inner Circle《Bad Boys》
#  36  摇滚     The Beatles《Hey Jude》、Nirvana《Smells Like Teen Spirit》、
#              Green Day《American Idiot》
#  37  原声/影视 John Williams《Hedwig's Theme》、Hans Zimmer《Time》
#   0  无曲风   treat as unidentified (do NOT default to 流行)
#
# Seen but left unmapped (inconsistent or too few independent samples):
#   2, 3 (jazz tracks AND Beethoven《Für Elise》 share 3), 16, 27 (Latin and
#   reggae mixed), 28 (some metal / nu-metal, inconsistent), 31 (Mozart and
#   Enya mixed), 39 (seen on 叶启田《爱拼才会赢》, not enough to call
#   "World Music"), 41 (only Sex Pistols in our sample), 50 (Billie Eilish
#   bad guy, Linkin Park Numb, Radiohead Creep, Arctic Monkeys — suggestive
#   of 独立/另类 but the same works also appear as 36/28/1), 51 (久石让
#   《いつも何度でも》 and LiSA《紅蓮華》; not consistently ACG or
#   soundtrack — 千本桜 is 1, 残酷な天使のテーゼ is 37), 58, 70.
QQ_GENRE_ID_TO_LABEL: dict[int, str] = {
    1: "流行",
    15: "蓝调",
    19: "乡村",
    20: "舞曲",
    22: "电子",
    23: "民谣",
    33: "R&B",
    34: "说唱",
    35: "雷鬼",
    36: "摇滚",
    37: "原声/影视",
}

# Verified song-level QQ `language` IDs, same date and endpoints.
#   0 国语   周杰伦《晴天》、赵雷《成都》
#   1 粤语   陈奕迅《富士山下》、BEYOND《光辉岁月》
#   2 闽南语 江蕙《家后》《酒后的心声》、张秀卿《车站》、叶启田《爱拼才会赢》
#   3 日语   米津玄师 Lemon、《打上花火》、初音未来《千本桜》
#   4 韩语   IU《좋은 날》
#   5 英语   Adele Hello、The Beatles《Hey Jude》
#   9 纯音乐 作为语种字段里的属性，而非口语语种。交叉验证：石进《夜的钢琴曲5
#            (纯音乐)》、贝多芬《月光奏鸣曲 (纯音乐)》、Für Elise、John Williams
#            《Hedwig's Theme》、Hans Zimmer《Time》，以及 Avicii《Levels
#            (Original Version)》等器乐版本。不确认的编号不当成语种。
#  16 西班牙语 Luis Fonsi《Despacito》、Ricky Martin《Livin' La Vida Loca》、
#            Enrique Iglesias《Bailando》、Los Del Rio《Macarena》
# Unmapped language IDs seen: 7, 10, 18, and others — left unidentified.
QQ_LANGUAGE_ID_TO_LABEL: dict[int, str] = {
    0: "国语",
    1: "粤语",
    2: "闽南语",
    3: "日语",
    4: "韩语",
    5: "英语",
    9: "纯音乐",
    16: "西班牙语",
}

# External tag names (Last.fm / MusicBrainz / leftover platform strings)
# fold into the same top-level labels. Unknown tags are ignored, never guessed.
_TAG_TO_GENRE: dict[str, str] = {
    # 流行
    "pop": "流行",
    "popular": "流行",
    "c-pop": "流行",
    "cpop": "流行",
    "mandopop": "流行",
    "cantopop": "流行",
    "k-pop": "流行",
    "kpop": "流行",
    "j-pop": "流行",
    "jpop": "流行",
    "synth-pop": "流行",
    "synthpop": "流行",
    "electropop": "流行",
    "electro-pop": "流行",
    "teen pop": "流行",
    "dance-pop": "流行",
    "dance pop": "流行",
    "pop rock": "流行",
    "pop punk": "朋克",
    "流行": "流行",
    "流行音乐": "流行",
    "华语流行": "流行",
    # 说唱
    "hip hop": "说唱",
    "hip-hop": "说唱",
    "hiphop": "说唱",
    "rap": "说唱",
    "trap": "说唱",
    "boom bap": "说唱",
    "gangsta rap": "说唱",
    "melodic rap": "说唱",
    "说唱": "说唱",
    "嘻哈": "说唱",
    # R&B
    "r&b": "R&B",
    "rnb": "R&B",
    "rhythm and blues": "R&B",
    "rhythm & blues": "R&B",
    "contemporary r&b": "R&B",
    "soul": "R&B",
    "neo soul": "R&B",
    "neo-soul": "R&B",
    "funk": "R&B",
    # 摇滚
    "rock": "摇滚",
    "classic rock": "摇滚",
    "hard rock": "摇滚",
    "soft rock": "摇滚",
    "folk rock": "民谣",
    "folk-rock": "民谣",
    "indie rock": "独立/另类",
    "alternative rock": "独立/另类",
    "prog rock": "摇滚",
    "progressive rock": "摇滚",
    "psychedelic rock": "摇滚",
    "摇滚": "摇滚",
    "摇滚乐": "摇滚",
    # 民谣
    "folk": "民谣",
    "folk music": "民谣",
    "singer-songwriter": "民谣",
    "singer songwriter": "民谣",
    "contemporary folk": "民谣",
    "民谣": "民谣",
    "民歌": "民谣",
    # 电子
    "electronic": "电子",
    "electronica": "电子",
    "edm": "电子",
    "techno": "电子",
    "house": "电子",
    "trance": "电子",
    "industrial": "电子",
    "ambient": "电子",
    "idm": "电子",
    "drum and bass": "电子",
    "drum & bass": "电子",
    "dubstep": "电子",
    "synthwave": "电子",
    "electronic music": "电子",
    "电子": "电子",
    "电子音乐": "电子",
    # 舞曲
    "dance": "舞曲",
    "disco": "舞曲",
    "club": "舞曲",
    "dance music": "舞曲",
    "house dance": "舞曲",
    "舞曲": "舞曲",
    # 国风
    "国风": "国风",
    "中国风": "国风",
    "古风": "国风",
    "chinese style": "国风",
    "guofeng": "国风",
    "zhongguofeng": "国风",
    # 爵士
    "jazz": "爵士",
    "bebop": "爵士",
    "swing": "爵士",
    "smooth jazz": "爵士",
    "vocal jazz": "爵士",
    "爵士": "爵士",
    "爵士乐": "爵士",
    # 蓝调
    "blues": "蓝调",
    "delta blues": "蓝调",
    "electric blues": "蓝调",
    "rhythm and blues blues": "蓝调",
    "蓝调": "蓝调",
    "布鲁斯": "蓝调",
    # 金属
    "metal": "金属",
    "heavy metal": "金属",
    "death metal": "金属",
    "black metal": "金属",
    "thrash": "金属",
    "thrash metal": "金属",
    "nu metal": "金属",
    "nu-metal": "金属",
    "metalcore": "金属",
    "doom metal": "金属",
    "金属": "金属",
    # 朋克
    "punk": "朋克",
    "punk rock": "朋克",
    "post-punk": "朋克",
    "post punk": "朋克",
    "hardcore punk": "朋克",
    "pop-punk": "朋克",
    "朋克": "朋克",
    # 独立/另类
    "indie": "独立/另类",
    "alternative": "独立/另类",
    "indie pop": "独立/另类",
    "indie-pop": "独立/另类",
    "britpop": "独立/另类",
    "alt-rock": "独立/另类",
    "alt rock": "独立/另类",
    "grunge": "独立/另类",
    "独立": "独立/另类",
    "独立音乐": "独立/另类",
    "另类": "独立/另类",
    "另类摇滚": "独立/另类",
    # 轻音乐
    "easy listening": "轻音乐",
    "light music": "轻音乐",
    "new age": "轻音乐",
    "new-age": "轻音乐",
    "lounge": "轻音乐",
    "轻音乐": "轻音乐",
    # 古典
    "classical": "古典",
    "classic": "古典",
    "orchestra": "古典",
    "orchestral": "古典",
    "symphony": "古典",
    "baroque": "古典",
    "opera": "古典",
    "piano classical": "古典",
    "classical music": "古典",
    "古典": "古典",
    "古典音乐": "古典",
    # 原声/影视
    "soundtrack": "原声/影视",
    "ost": "原声/影视",
    "film score": "原声/影视",
    "film soundtrack": "原声/影视",
    "movie soundtrack": "原声/影视",
    "tv soundtrack": "原声/影视",
    "musical": "原声/影视",
    "score": "原声/影视",
    "original soundtrack": "原声/影视",
    "影视": "原声/影视",
    "原声": "原声/影视",
    "原声带": "原声/影视",
    "影视原声": "原声/影视",
    # ACG
    "acg": "ACG",
    "anime": "ACG",
    "animation": "ACG",
    "vocaloid": "ACG",
    "otaku": "ACG",
    "anime song": "ACG",
    "anime music": "ACG",
    "video game music": "ACG",
    "game music": "ACG",
    "j-idol": "ACG",
    "动漫": "ACG",
    "动画": "ACG",
    "游戏": "ACG",
    # 乡村
    "country": "乡村",
    "country pop": "乡村",
    "country rock": "乡村",
    "bluegrass": "乡村",
    "乡村": "乡村",
    "乡村音乐": "乡村",
    # 雷鬼
    "reggae": "雷鬼",
    "dancehall": "雷鬼",
    "dub": "雷鬼",
    "reggaeton": "拉丁",
    "雷鬼": "雷鬼",
    # 拉丁
    "latin": "拉丁",
    "latino": "拉丁",
    "salsa": "拉丁",
    "bachata": "拉丁",
    "merengue": "拉丁",
    "samba": "拉丁",
    "bossa nova": "拉丁",
    "tango": "拉丁",
    "latin pop": "拉丁",
    "西班牙语": "拉丁",
    "拉丁": "拉丁",
    # 世界音乐
    "world": "世界音乐",
    "world music": "世界音乐",
    "worldbeat": "世界音乐",
    "celtic": "世界音乐",
    "african": "世界音乐",
    "world fusion": "世界音乐",
    "世界音乐": "世界音乐",
    "世界": "世界音乐",
}

_TAG_NOISE = frozenset({
    "love", "beautiful", "awesome", "favorite", "favourites", "favorites",
    "seen live", "male vocalists", "female vocalists", "male vocalist",
    "female vocalist", "00s", "10s", "20s", "60s", "70s", "80s", "90s",
    "2010s", "2000s", "1990s", "1980s", "1970s", "1960s", "2020s",
    "chinese", "english", "japanese", "korean", "american", "british",
    "uk", "usa", "vocal", "vocals", "instrumental", "ballad", "sad",
    "happy", "chill", "cool", "sexy", "party", "summer", "winter",
    "night", "relax", "mood", "recommended", "hot", "top", "hit", "hits",
    "classic",  # too ambiguous vs classical; "classical" is mapped above
})

_LANGUAGE_ALIASES: dict[str, str] = {
    "国语": "国语",
    "普通话": "国语",
    "mandarin": "国语",
    "chinese": "国语",
    "zh": "国语",
    "zh-cn": "国语",
    "粤语": "粤语",
    "cantonese": "粤语",
    "广东话": "粤语",
    "zh-hk": "粤语",
    "zh-yue": "粤语",
    "闽南语": "闽南语",
    "台语": "闽南语",
    "台湾闽南语": "闽南语",
    "hokkien": "闽南语",
    "minnan": "闽南语",
    "taiwanese": "闽南语",
    "英语": "英语",
    "英文": "英语",
    "english": "英语",
    "en": "英语",
    "日语": "日语",
    "日文": "日语",
    "japanese": "日语",
    "jp": "日语",
    "ja": "日语",
    "韩语": "韩语",
    "韩文": "韩语",
    "korean": "韩语",
    "ko": "韩语",
    "kr": "韩语",
    "西班牙语": "西班牙语",
    "西语": "西班牙语",
    "spanish": "西班牙语",
    "es": "西班牙语",
    "纯音乐": "纯音乐",
    "器乐": "纯音乐",
    "instrumental": "纯音乐",
    "instrumental music": "纯音乐",
}

# Modest traditional→simplified map for artist/title matching (not exhaustive).
_TRAD_SIMP = str.maketrans({
    "樂": "乐", "後": "后", "時": "时", "東": "东", "來": "来", "風": "风",
    "陳": "陈", "張": "张", "劉": "刘", "黃": "黄", "楊": "杨", "許": "许",
    "鄧": "邓", "蕭": "萧", "葉": "叶", "萬": "万", "點": "点", "愛": "爱",
    "語": "语", "專": "专", "門": "门", "關": "关", "於": "于", "麼": "么",
    "為": "为", "這": "这", "與": "与", "無": "无", "師": "师", "條": "条",
    "個": "个", "們": "们", "說": "说", "國": "国", "會": "会", "對": "对",
    "開": "开", "聲": "声", "長": "长", "電": "电", "網": "网", "過": "过",
    "還": "还", "從": "从", "當": "当", "經": "经", "現": "现", "發": "发",
    "學": "学", "業": "业", "樣": "样", "機": "机", "實": "实", "戰": "战",
    "進": "进", "種": "种", "動": "动", "華": "华", "衛": "卫", "傑": "杰",
    "倫": "伦", "韻": "韵", "詞": "词", "頭": "头", "歡": "欢", "聽": "听",
    "夢": "梦", "離": "离", "處": "处", "號": "号", "歲": "岁", "衝": "冲",
    "獨": "独", "憶": "忆", "戀": "恋", "兒": "儿", "島": "岛", "鐘": "钟",
    "龍": "龙", "鳳": "凤", "寶": "宝", "麗": "丽", "訊": "讯", "軒": "轩",
    "銘": "铭", "偉": "伟", "濤": "涛", "凱": "凯", "達": "达", "遠": "远",
    "嗎": "吗", "唄": "呗", "興": "兴", "裡": "里", "裏": "里", "齣": "出",
    "臺": "台", "灣": "湾", "書": "书", "畫": "画", "圖": "图",
    "藝": "艺", "術": "术", "團": "团", "隊": "队", "員": "员", "將": "将",
    "則": "则", "並": "并", "細": "细", "幾": "几", "處": "处", "體": "体",
    "親": "亲", "屬": "属", "總": "总", "約": "约", "紀": "纪", "年": "年",
    "歲": "岁", "馬": "马", "鳥": "鸟", "魚": "鱼", "麥": "麦", "麵": "面",
    "髮": "发", "鬆": "松", "採": "采", "捨": "舍", "捨": "舍", "衝": "冲",
    "儘": "尽", "儘": "尽", "儘": "尽", "盡": "尽",
    "迴": "回", "迴": "回", "迴": "回",
    "雲": "云", "電": "电", "燈": "灯", "場": "场", "壞": "坏", "報": "报",
    "導": "导", "幣": "币", "幹": "干", "廣": "广", "慶": "庆", "廳": "厅",
    "庫": "库", "錄": "录", "證": "证", "議": "议", "調": "调", "談": "谈",
    "謝": "谢", "讓": "让", "識": "识", "讀": "读", "變": "变", "連": "连",
    "選": "选", "過": "过", "適": "适", "遷": "迁", "遞": "递", "遲": "迟",
    "鄉": "乡", "鄰": "邻", "醜": "丑", "鍵": "键", "鍾": "钟", "鐵": "铁",
    "鑑": "鉴", "錢": "钱", "銀": "银", "鈴": "铃", "銷": "销", "鎖": "锁",
    "鎮": "镇", "關": "关", "際": "际", "隨": "随", "隱": "隐", "隻": "只",
    "雖": "虽", "雙": "双", "雜": "杂", "雞": "鸡", "離": "离", "難": "难",
    "雲": "云", "靈": "灵", "青": "青", "靜": "静", "順": "顺", "須": "须",
    "頌": "颂", "頑": "顽", "頓": "顿", "領": "领", "頭": "头", "頻": "频",
    "題": "题", "額": "额", "顏": "颜", "願": "愿", "類": "类", "風": "风",
    "飛": "飞", "飯": "饭", "飲": "饮", "飾": "饰", "養": "养", "餘": "余",
    "館": "馆", "馬": "马", "駐": "驻", "駕": "驾", "驗": "验", "驚": "惊",
    "體": "体", "高": "高", "鬥": "斗", "鬧": "闹", "鷗": "鸥", "麥": "麦",
    "麼": "么", "黃": "黄", "點": "点", "齊": "齐", "龍": "龙", "龐": "庞",
    "龔": "龚", "鬱": "郁", "豔": "艳", "靈": "灵", "齣": "出", "樹": "树",
    "橋": "桥", "機": "机", "權": "权", "樓": "楼", "標": "标", "檻": "槛",
    "歐": "欧", "歎": "叹", "歡": "欢", "歲": "岁", "歷": "历", "歸": "归",
    "殘": "残", "殺": "杀", "殼": "壳", "毀": "毁", "畢": "毕", "彙": "汇",
    "漢": "汉", "滿": "满", "漸": "渐", "濾": "滤", "濕": "湿", "濟": "济",
    "濤": "涛", "濃": "浓", "濱": "滨", "瀏": "浏", "瀧": "泷", "瀨": "濑",
    "災": "灾", "為": "为", "無": "无", "煉": "炼", "煙": "烟", "熱": "热",
    "燈": "灯", "燒": "烧", "營": "营", "爭": "争", "牆": "墙", "獎": "奖",
    "瑪": "玛", "環": "环", "瓊": "琼", "瓦": "瓦", "產": "产", "異": "异",
    "當": "当", "疇": "畴", "疊": "叠", "癡": "痴", "癡": "痴", "瘋": "疯",
    "療": "疗", "癡": "痴", "發": "发", "盜": "盗", "監": "监", "盤": "盘",
    "盧": "卢", "眞": "真", "眾": "众", "睏": "困", "睜": "睁", "瞭": "了",
    "矯": "矫", "硃": "朱", "硏": "研", "確": "确", "礙": "碍", "禮": "礼",
    "禱": "祷", "離": "离", "種": "种", "稱": "称", "穩": "稳", "窩": "窝",
    "窮": "穷", "窺": "窥", "競": "竞", "筆": "笔", "筍": "笋", "筧": "笕",
    "箋": "笺", "節": "节", "範": "范", "築": "筑", "篤": "笃", "篩": "筛",
    "篋": "箧", "簡": "简", "簫": "箫", "簽": "签", "簾": "帘", "簿": "簿",
    "籍": "籍", "粉": "粉", "粗": "粗", "粧": "妆", "糧": "粮", "繫": "系",
    "糾": "纠", "紀": "纪", "約": "约", "純": "纯", "紗": "纱", "納": "纳",
    "紙": "纸", "紛": "纷", "紡": "纺", "紋": "纹", "紡": "纺", "紐": "纽",
    "組": "组", "細": "细", "終": "终", "絆": "绊", "結": "结", "絕": "绝",
    "絡": "络", "給": "给", "絨": "绒", "統": "统", "絲": "丝", "經": "经",
    "綜": "综", "綠": "绿", "維": "维", "綱": "纲", "網": "网", "綵": "彩",
    "綸": "纶", "綺": "绮", "綻": "绽", "綽": "绰", "綿": "绵", "緊": "紧",
    "緋": "绯", "緒": "绪", "線": "线", "緝": "缉", "緞": "缎", "締": "缔",
    "緣": "缘", "編": "编", "緩": "缓", "緬": "缅", "緯": "纬", "練": "练",
    "縣": "县", "縫": "缝", "縱": "纵", "縧": "绦", "縝": "缜", "縟": "缛",
    "縣": "县", "縮": "缩", "績": "绩", "繁": "繁", "纖": "纤", "纏": "缠",
    "續": "续", "蘭": "兰", "蘇": "苏", "蘆": "芦", "蘋": "苹", "蘭": "兰",
    "蕭": "萧", "蔣": "蒋", "營": "营", "處": "处", "虛": "虚", "號": "号",
    "虧": "亏", "蟲": "虫", "雖": "虽", "蝦": "虾", "蝶": "蝶", "蟬": "蝉",
    "蟻": "蚁", "眾": "众", "術": "术", "衛": "卫", "衝": "冲", "袞": "衮",
    "補": "补", "製": "制", "複": "复", "褲": "裤", "褻": "亵", "襯": "衬",
    "襲": "袭", "西": "西", "見": "见", "規": "规", "視": "视", "親": "亲",
    "覺": "觉", "覽": "览", "觀": "观", "角": "角", "解": "解", "觸": "触",
    "言": "言", "訂": "订", "計": "计", "訊": "讯", "討": "讨", "訓": "训",
    "託": "托", "記": "记", "訟": "讼", "訣": "诀", "訪": "访", "設": "设",
    "許": "许", "訴": "诉", "評": "评", "詞": "词", "詢": "询", "試": "试",
    "詩": "诗", "話": "话", "該": "该", "詳": "详", "誇": "夸", "譽": "誉",
    "誌": "志", "認": "认", "誓": "誓", "誕": "诞", "語": "语", "誠": "诚",
    "誡": "诫", "誤": "误", "誘": "诱", "誨": "诲", "說": "说", "誰": "谁",
    "課": "课", "誼": "谊", "調": "调", "諒": "谅", "論": "论", "諜": "谍",
    "諦": "谛", "諫": "谏", "諭": "谕", "諮": "咨", "諱": "讳", "諳": "谙",
    "諷": "讽", "諸": "诸", "諺": "谚", "諾": "诺", "謀": "谋", "謁": "谒",
    "謂": "谓", "謄": "誊", "謎": "谜", "謐": "谧", "謙": "谦", "講": "讲",
    "謝": "谢", "謠": "谣", "謨": "谟", "謹": "谨", "證": "证", "識": "识",
    "譜": "谱", "警": "警", "譯": "译", "議": "议", "護": "护", "譽": "誉",
    "讀": "读", "變": "变", "讓": "让", "讚": "赞", "豎": "竖", "豐": "丰",
    "豔": "艳", "豬": "猪", "貓": "猫", "貝": "贝", "貞": "贞", "負": "负",
    "財": "财", "貢": "贡", "貧": "贫", "貨": "货", "販": "贩", "貪": "贪",
    "貫": "贯", "責": "责", "貯": "贮", "貳": "贰", "貴": "贵", "費": "费",
    "賀": "贺", "貽": "贻", "賊": "贼", "賈": "贾", "資": "资", "賡": "赓",
    "賑": "赈", "賚": "赉", "賞": "赏", "賦": "赋", "質": "质", "賬": "账",
    "賭": "赌", "賴": "赖", "賺": "赚", "購": "购", "賽": "赛", "贅": "赘",
    "贈": "赠", "贊": "赞", "贏": "赢", "贓": "赃", "贖": "赎", "贛": "赣",
    "趙": "赵", "趣": "趣", "趨": "趋", "跡": "迹", "踐": "践", "踴": "踊",
    "蹣": "蹒", "蹤": "踪", "軀": "躯", "車": "车", "軌": "轨", "軍": "军",
    "軒": "轩", "軟": "软", "較": "较", "載": "载", "輒": "辄", "輔": "辅",
    "輕": "轻", "輝": "辉", "輛": "辆", "輟": "辍", "輩": "辈", "輪": "轮",
    "輯": "辑", "輸": "输", "輻": "辐", "轄": "辖", "轅": "辕", "轉": "转",
    "轍": "辙", "轎": "轿", "轟": "轰", "辯": "辩", "邊": "边", "達": "达",
    "遊": "游", "運": "运", "過": "过", "道": "道", "達": "达", "違": "违",
    "遙": "遥", "遜": "逊", "遠": "远", "適": "适", "遭": "遭", "遮": "遮",
    "遲": "迟", "選": "选", "遺": "遗", "遼": "辽", "還": "还", "邁": "迈",
    "還": "还", "邇": "迩", "邊": "边", "邏": "逻", "邠": "彬", "鄧": "邓",
    "鄭": "郑", "鄰": "邻", "鄺": "邝", "配": "配", "酒": "酒", "醜": "丑",
    "醫": "医", "醬": "酱", "釀": "酿", "釋": "释", "金": "金", "釘": "钉",
    "針": "针", "釣": "钓", "釵": "钗", "鈣": "钙", "鈍": "钝", "鈔": "钞",
    "鐘": "钟", "鈉": "钠", "鋼": "钢", "鑰": "钥", "欽": "钦", "鈞": "钧",
    "鎢": "钨", "鉤": "钩", "鈕": "钮", "鈺": "钰", "鉑": "铂", "鉗": "钳",
    "鉛": "铅", "鉤": "钩", "鉸": "铰", "銀": "银", "鑄": "铸", "鋪": "铺",
    "鍊": "链", "銷": "销", "鎖": "锁", "鋤": "锄", "鍋": "锅", "銹": "锈",
    "鋒": "锋", "銳": "锐", "錯": "错", "錫": "锡", "錘": "锤", "錦": "锦",
    "錠": "锭", "鍵": "键", "鍼": "针", "鍾": "钟", "鎊": "镑", "鎬": "镐",
    "鏈": "链", "鏟": "铲", "鏡": "镜", "鏢": "镖", "鐘": "钟", "鐮": "镰",
    "鐵": "铁", "鑑": "鉴", "鑒": "鉴", "鑣": "镳", "鑫": "鑫", "隱": "隐",
    "隸": "隶", "雖": "虽", "雙": "双", "雜": "杂", "雞": "鸡", "離": "离",
    "難": "难", "雲": "云", "電": "电", "需": "需", "霆": "霆", "震": "震",
    "霍": "霍", "霧": "雾", "霽": "霁", "靂": "雳", "靄": "霭", "靈": "灵",
    "青": "青", "靖": "靖", "靜": "静", "非": "非", "面": "面", "靨": "靥",
    "革": "革", "靳": "靳", "靴": "靴", "靶": "靶", "鞋": "鞋", "鞏": "巩",
    "韃": "鞑", "韋": "韦", "韓": "韩", "韜": "韬", "音": "音", "頁": "页",
    "頂": "顶", "頃": "顷", "項": "项", "順": "顺", "須": "须", "頌": "颂",
    "預": "预", "頑": "顽", "頒": "颁", "頓": "顿", "頗": "颇", "領": "领",
    "頸": "颈", "頰": "颊", "頻": "频", "頭": "头", "頰": "颊", "題": "题",
    "額": "额", "顏": "颜", "願": "愿", "顛": "颠", "類": "类", "顧": "顾",
    "顫": "颤", "顯": "显", "風": "风", "颯": "飒", "颱": "台", "颳": "刮",
    "飄": "飘", "飆": "飙", "飛": "飞", "食": "食", "飢": "饥", "飯": "饭",
    "飲": "饮", "飽": "饱", "飾": "饰", "餃": "饺", "餅": "饼", "養": "养",
    "餌": "饵", "餓": "饿", "餘": "余", "館": "馆", "餵": "喂", "餾": "馏",
    "饅": "馒", "饃": "馍", "首": "首", "香": "香", "馬": "马", "馭": "驭",
    "馮": "冯", "馱": "驮", "馳": "驰", "馴": "驯", "駐": "驻", "駒": "驹",
    "駕": "驾", "駛": "驶", "駝": "驼", "駟": "驷", "騁": "骋", "驗": "验",
    "駿": "骏", "騎": "骑", "騙": "骗", "騷": "骚", "驅": "驱", "驊": "骅",
    "驚": "惊", "驛": "驿", "驟": "骤", "驢": "驴", "驥": "骥", "骨": "骨",
    "骯": "肮", "髒": "脏", "體": "体", "高": "高", "鬥": "斗", "鬧": "闹",
    "鬼": "鬼", "魁": "魁", "魂": "魂", "魅": "魅", "魄": "魄", "魔": "魔",
    "魚": "鱼", "魯": "鲁", "鮮": "鲜", "鯉": "鲤", "鯊": "鲨", "鯨": "鲸",
    "鱗": "鳞", "鳥": "鸟", "鳳": "凤", "鳴": "鸣", "鳶": "鸢", "鴟": "鸱",
    "鴨": "鸭", "鴻": "鸿", "鴿": "鸽", "鵲": "鹊", "鵬": "鹏", "鶴": "鹤",
    "鷹": "鹰", "鷺": "鹭", "鹽": "盐", "鹿": "鹿", "麥": "麦", "麴": "曲",
    "麵": "面", "麻": "麻", "黃": "黄", "黍": "黍", "黎": "黎", "黑": "黑",
    "默": "默", "點": "点", "黨": "党", "黯": "黯", "黴": "霉", "鼇": "鳌",
    "鼎": "鼎", "鼓": "鼓", "鼠": "鼠", "鼻": "鼻", "齊": "齐", "齒": "齿",
    "齡": "龄", "龍": "龙", "龐": "庞", "龔": "龚", "龜": "龟", "龢": "和",
    "憂": "忧", "優": "优", "傷": "伤", "僅": "仅", "僕": "仆", "僑": "侨",
    "僞": "伪", "傘": "伞", "傭": "佣", "債": "债", "傷": "伤", "傾": "倾",
    "僅": "仅", "僉": "佥", "傳": "传", "債": "债", "傷": "伤", "傻": "傻",
    "傾": "倾", "僂": "偻", "僅": "仅", "僉": "佥", "像": "像", "僕": "仆",
    "僑": "侨", "僞": "伪", "僥": "侥", "僱": "雇", "僵": "僵", "價": "价",
    "儀": "仪", "儂": "侬", "億": "亿", "儉": "俭", "儅": "当", "儒": "儒",
    "儘": "尽", "償": "偿", "優": "优", "儲": "储", "儷": "俪", "儻": "傥",
    "儼": "俨", "兌": "兑", "兒": "儿", "兗": "兖", "內": "内", "兩": "两",
    "冊": "册", "冪": "幂", "凍": "冻", "凱": "凯", "別": "别", "刪": "删",
    "剄": "刭", "則": "则", "剉": "锉", "剋": "克", "剛": "刚", "剝": "剥",
    "剮": "剐", "剴": "剀", "創": "创", "劃": "划", "劇": "剧", "劉": "刘",
    "劊": "刽", "劌": "刿", "劍": "剑", "劑": "剂", "勌": "倦", "勛": "勋",
    "勝": "胜", "勞": "劳", "勢": "势", "勣": "绩", "勦": "剿", "勩": "勚",
    "勱": "劢", "勳": "勋", "勵": "励", "勸": "劝", "勻": "匀", "匭": "匦",
    "匯": "汇", "匱": "匮", "區": "区", "協": "协", "卹": "恤", "卻": "却",
    "厙": "厍", "厭": "厌", "厲": "厉", "厴": "厣", "參": "参", "叢": "丛",
})

DEFAULT_CACHE_PATH = Path(__file__).resolve().parent / "data" / "genre-cache.json"
DEFAULT_ENRICH_BUDGET_S = 22.0
DEFAULT_QQ_WORKERS = 4
MIN_GENRE_TRACKS = 8
MIN_GENRE_COVERAGE = 0.30

QQ_SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
QQ_DETAIL_URL = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg"
QQ_REFERER = "https://y.qq.com/"
MB_USER_AGENT = "playlist-report/0.1 (local educational MVP; not for commercial use)"
_CACHE_LOCK = threading.Lock()
_MB_LOCK = threading.Lock()
_MB_LAST_REQUEST = 0.0

FetchJson = Callable[..., Any]


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

def _coerce_int(value: Any) -> int | None:
    if value is None or value is False or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def map_qq_genre_id(value: Any) -> str | None:
    """Map a song-level QQ genre id. Unknown / 0 / unmapped → None (never 流行)."""
    gid = _coerce_int(value)
    if gid is None:
        return None
    return QQ_GENRE_ID_TO_LABEL.get(gid)


def map_qq_language_id(value: Any) -> str | None:
    lid = _coerce_int(value)
    if lid is None:
        return None
    return QQ_LANGUAGE_ID_TO_LABEL.get(lid)


def _normalize_tag_key(tag: str) -> str:
    text = unicodedata.normalize("NFKC", str(tag)).strip().lower()
    text = text.replace("＆", "&").replace("／", "/")
    text = re.sub(r"[_./]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def map_tag(tag: str | None) -> str | None:
    """Map a Last.fm / MusicBrainz / free-text tag onto a canonical genre, or None."""
    if not tag or not isinstance(tag, str):
        return None
    key = _normalize_tag_key(tag)
    if not key or key in _TAG_NOISE:
        return None
    if key in _GENRE_SET:
        return key
    mapped = _TAG_TO_GENRE.get(key)
    if mapped:
        return mapped
    # Mild fallback: leading token ("indie rock (sweden)" already normalized)
    if key.endswith(" music"):
        mapped = _TAG_TO_GENRE.get(key[: -len(" music")])
        if mapped:
            return mapped
    return None


def map_tags(tags: Any) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    if isinstance(tags, str):
        raw = [tags]
    elif isinstance(tags, (list, tuple)):
        raw = list(tags)
    else:
        raw = []
    for item in raw:
        name = item
        if isinstance(item, dict):
            name = item.get("name") or item.get("genre") or item.get("title")
        label = map_tag(str(name) if name is not None else None)
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return labels


def map_language_label(value: Any) -> str | None:
    if value is None or value is False:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return map_qq_language_id(value)
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text:
        return None
    if text in _LANGUAGE_SET:
        return text
    return _LANGUAGE_ALIASES.get(_normalize_tag_key(text))


def unique_labels(labels: list[str], allowed: frozenset[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for label in labels:
        if label in allowed and label not in seen:
            seen.add(label)
            out.append(label)
    return out


# ---------------------------------------------------------------------------
# Normalization / strict matching
# ---------------------------------------------------------------------------

_VERSION_TOKENS = frozenset({
    "live", "remix", "rmx", "remaster", "remastered", "explicit", "clean",
    "deluxe", "bonus", "acoustic", "instrumental", "karaoke", "extended",
    "edit", "version", "mix", "radio", "album", "single", "original",
    "club", "radioedit", "radioversion", "albumversion", "singleversion",
    "originalmix", "originalversion", "clubmix", "bonustrack",
    "digital", "lp", "ep", "dj", "feat", "featuring", "ft", "with",
    "cover", "piano", "inst", "mono", "stereo", "theme", "from",
    "complete", "short", "long", "full", "official", "audio", "lyric",
    "video", "session", "demo", "reprise", "cut", "dub",
    "演唱会", "现场", "重制", "伴奏", "纯音乐", "高音质版", "数字重制",
    "电台版", "完整版", "短版", "加速版", "现场版", "演唱会版", "录音室",
    "纪念版", "豪华版", "特别版", "升级版", "改版", "remix版", "live版",
    "dj版", "lp版", "旧版", "新版", "日文版", "中文版", "英文版",
    "粤语版", "国语版", "韩文版", "日语版", "电视版", "电影版",
    "片头", "片尾", "插曲", "主题曲", "op", "ed", "tv", "ost",
})

_FEAT_PREFIX = re.compile(
    r"[\s\-]*(?:feat\.?|ft\.?|featuring|with)\s+.+$",
    re.I,
)

_BRACKETS = re.compile(r"[\(（\[【]([^\)）\]】]{0,80})[\)）\]】]")


def _fold_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.translate(_TRAD_SIMP)
    return text.lower()


def _is_version_attr(inner: str) -> bool:
    raw = _fold_text(inner)
    raw = raw.replace("|", " ").replace("/", " ").replace("&", " ")
    raw = raw.replace("feat.", "feat ").replace("ft.", "ft ")
    # Years and punctuation out; leftover should be version words, or start with one.
    if re.search(
        r"\b(live|remix|remaster(?:ed)?|explicit|acoustic|instrumental|"
        r"karaoke|radio\s*edit|original\s*(?:mix|version)|bonus\s*track|"
        r"feat\.?|featuring)\b",
        raw,
        re.I,
    ):
        return True
    if re.search(r"(现场|演唱会|纯音乐|伴奏|重制|高音质|dj版|lp版)", raw):
        return True
    tokens = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", raw)
    if not tokens:
        return True
    meaningful = [tok for tok in tokens if not re.fullmatch(r"\d{2,4}", tok)]
    if not meaningful:
        return True
    if all(tok in _VERSION_TOKENS or re.fullmatch(r"v\d+", tok) for tok in meaningful):
        return True
    # "Love Version", "Japanese Version", "主题版"
    last = meaningful[-1]
    if last in {"version", "mix", "edition", "edit", "remaster", "remastered"} or last.endswith("版"):
        return True
    return False


def _strip_version_brackets(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return " " if _is_version_attr(match.group(1)) else match.group(0)

    prev = None
    out = text
    while out != prev:
        prev = out
        out = _BRACKETS.sub(repl, out)
    return out


def _collapse_name(text: str) -> str:
    text = _fold_text(text)
    text = _strip_version_brackets(text)
    text = _FEAT_PREFIX.sub("", text)
    text = re.sub(r"[\s\-–—_~,，。!！?？:：;；'\"“”‘’`.·•*]+", "", text)
    return text.strip()


def normalize_title(name: str | None) -> str:
    return _collapse_name(name or "")


def normalize_artist(name: str | None) -> str:
    text = _fold_text(name or "")
    text = _strip_version_brackets(text)
    text = re.sub(r"^the\s+", "", text)
    text = _FEAT_PREFIX.sub("", text)
    text = re.sub(r"[\s\-–—_~,，。!！?？:：;；'\"“”‘’`.·•*]+", "", text)
    return text.strip()


def titles_match(left: str | None, right: str | None) -> bool:
    a, b = normalize_title(left), normalize_title(right)
    return bool(a) and a == b


def artist_names(value: Any) -> list[str]:
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("title") or "").strip()
        elif item is None:
            name = ""
        else:
            name = str(item).strip()
        if not name:
            continue
        key = normalize_artist(name)
        if not key or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def artists_match(left: Any, right: Any) -> bool:
    left_keys = {normalize_artist(n) for n in artist_names(left)}
    right_keys = {normalize_artist(n) for n in artist_names(right)}
    left_keys.discard("")
    right_keys.discard("")
    return bool(left_keys and right_keys and left_keys.intersection(right_keys))


def cache_key(name: str | None, artists: Any) -> str:
    title = normalize_title(name)
    arts = sorted({normalize_artist(n) for n in artist_names(artists)} - {""})
    return f"{title}|{';'.join(arts)}"


# ---------------------------------------------------------------------------
# Local resolution from already-fetched fields
# ---------------------------------------------------------------------------

def genres_from_track(track: dict) -> list[str]:
    if not isinstance(track, dict):
        return []
    labels: list[str] = []
    if track.get("genres") is not None:
        labels.extend(map_tags(track.get("genres")))
    # A single pre-mapped string
    if isinstance(track.get("genre"), str):
        labels.extend(map_tags([track.get("genre")]))
    mapped = map_qq_genre_id(track.get("genreId") if "genreId" in track else track.get("genre"))
    if mapped:
        labels.append(mapped)
    return unique_labels(labels, _GENRE_SET)


def language_from_track(track: dict) -> str | None:
    if not isinstance(track, dict):
        return None
    if "language" in track and track.get("language") not in (None, ""):
        mapped = map_language_label(track.get("language"))
        if mapped:
            return mapped
        # Numeric language stored in `language` (QQ raw).
        mapped = map_qq_language_id(track.get("language"))
        if mapped:
            return mapped
    if "languageId" in track:
        mapped = map_qq_language_id(track.get("languageId"))
        if mapped:
            return mapped
    return None


def _track_has_mappable_genre(track: dict) -> bool:
    return bool(genres_from_track(track))


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

def default_cache_path() -> Path:
    return DEFAULT_CACHE_PATH


def load_cache(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else DEFAULT_CACHE_PATH
    if not target.is_file():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"version": 1, "entries": {}}
    if not isinstance(data, dict):
        return {"version": 1, "entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        data["entries"] = {}
    data.setdefault("version", 1)
    return data


def save_cache(data: dict[str, Any], path: Path | None = None) -> None:
    target = Path(path) if path is not None else DEFAULT_CACHE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(prefix=".genre-cache-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _entry_genres(entry: dict) -> list[str]:
    return unique_labels(list(entry.get("genres") or []), _GENRE_SET)


def _entry_language(entry: dict) -> str | None:
    return map_language_label(entry.get("language"))


# ---------------------------------------------------------------------------
# HTTP helpers for enrichment
# ---------------------------------------------------------------------------

def _default_qq_fetch(url: str, *, referer: str | None = None) -> Any:
    from fetchers import http_get_json

    return http_get_json(url, referer=referer)


def _external_json(url: str, *, user_agent: str, timeout: float = 15.0) -> Any:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/json",
    }
    req = Request(url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2 * 1024 * 1024)
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("invalid json") from exc


def _deadline_passed(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


# ---------------------------------------------------------------------------
# QQ public search + detail (no custom signatures)
# ---------------------------------------------------------------------------

def _qq_search_url(query: str, n: int = 5) -> str:
    return (
        f"{QQ_SEARCH_URL}?format=json&p=1&n={int(n)}&w={quote(query)}"
    )


def _qq_detail_url(songmid: str) -> str:
    return f"{QQ_DETAIL_URL}?tpl=yqq_song_detail&format=json&songmid={quote(songmid)}"


def pick_strict_qq_match(name: str, artists: Any, results: list[Any]) -> dict | None:
    """Return the first search hit whose title and at least one artist strictly match."""
    for item in results or []:
        if not isinstance(item, dict):
            continue
        title = item.get("songname") or item.get("name") or item.get("title") or ""
        singers = item.get("singer") or item.get("singers") or []
        singer_names = artist_names(singers)
        if item.get("singername") and not singer_names:
            singer_names = artist_names(item.get("singername"))
        if titles_match(name, str(title)) and artists_match(artists, singer_names):
            return item
    return None


def _query_qq_track(
    name: str,
    artists: Any,
    fetch_json: FetchJson,
) -> dict[str, Any]:
    arts = artist_names(artists)
    query = name if not arts else f"{name} {arts[0]}"
    data = fetch_json(_qq_search_url(query, n=5), referer=QQ_REFERER)
    songs = ((data or {}).get("data") or {}).get("song") or {}
    results = songs.get("list") or []
    hit = pick_strict_qq_match(name, artists, results)
    if not hit:
        return {"status": "miss", "genres": [], "language": None, "source": "qq"}
    mid = hit.get("songmid") or hit.get("mid")
    if not mid:
        return {"status": "miss", "genres": [], "language": None, "source": "qq"}
    detail = fetch_json(_qq_detail_url(str(mid)), referer=QQ_REFERER)
    rec = ((detail or {}).get("data") or [None])[0] or {}
    if not isinstance(rec, dict):
        rec = {}
    # Confirm detail still refers to the same work when names are present.
    det_name = rec.get("name") or rec.get("title") or rec.get("songname") or hit.get("songname")
    det_artists = rec.get("singer") or rec.get("singers") or hit.get("singer")
    if det_name and not titles_match(name, str(det_name)):
        return {"status": "miss", "genres": [], "language": None, "source": "qq"}
    if det_artists and not artists_match(artists, det_artists):
        return {"status": "miss", "genres": [], "language": None, "source": "qq"}
    genre_id = _coerce_int(rec.get("genre"))
    language_id = _coerce_int(rec.get("language"))
    label = map_qq_genre_id(genre_id)
    lang = map_qq_language_id(language_id)
    return {
        "status": "hit" if (label or lang is not None or genre_id is not None) else "miss",
        "genres": [label] if label else [],
        "language": lang,
        "qqGenreId": genre_id,
        "qqLanguageId": language_id,
        "source": "qq",
    }


# ---------------------------------------------------------------------------
# MusicBrainz / Last.fm fallbacks
# ---------------------------------------------------------------------------

def _mb_wait(deadline: float | None) -> bool:
    """Enforce 1 req/s. Return False if the deadline would be missed."""
    global _MB_LAST_REQUEST
    with _MB_LOCK:
        now = time.monotonic()
        wait = 1.0 - (now - _MB_LAST_REQUEST)
        if wait > 0:
            if deadline is not None and now + wait >= deadline:
                return False
            time.sleep(wait)
        if _deadline_passed(deadline):
            return False
        _MB_LAST_REQUEST = time.monotonic()
        return True


def _query_musicbrainz(name: str, artists: Any, deadline: float | None) -> dict[str, Any] | None:
    if _deadline_passed(deadline) or not _mb_wait(deadline):
        return None
    arts = artist_names(artists)
    artist = arts[0] if arts else ""
    query = f'recording:"{name}"'
    if artist:
        query += f' AND artist:"{artist}"'
    url = "https://musicbrainz.org/ws/2/recording/?" + urlencode(
        {"query": query, "fmt": "json", "limit": "5"}
    )
    try:
        data = _external_json(url, user_agent=MB_USER_AGENT)
    except RuntimeError:
        return {"status": "miss", "genres": [], "language": None, "source": "musicbrainz"}
    recordings = data.get("recordings") or []
    chosen = None
    for rec in recordings:
        if not isinstance(rec, dict):
            continue
        title = rec.get("title") or ""
        credits = rec.get("artist-credit") or []
        names = []
        for cred in credits:
            if isinstance(cred, dict):
                artist_obj = cred.get("artist") or {}
                names.append(cred.get("name") or artist_obj.get("name") or "")
        if titles_match(name, title) and artists_match(artists, names):
            chosen = rec
            break
    if not chosen or not chosen.get("id"):
        return {"status": "miss", "genres": [], "language": None, "source": "musicbrainz"}
    if not _mb_wait(deadline):
        return None  # incomplete; do not cache as a final miss
    lookup = f"https://musicbrainz.org/ws/2/recording/{quote(str(chosen['id']))}?inc=genres+tags&fmt=json"
    try:
        detail = _external_json(lookup, user_agent=MB_USER_AGENT)
    except RuntimeError:
        return {"status": "miss", "genres": [], "language": None, "source": "musicbrainz"}
    raw_tags: list[str] = []
    for bucket in (detail.get("genres") or []), (detail.get("tags") or []):
        for item in bucket:
            if isinstance(item, dict) and item.get("name"):
                raw_tags.append(str(item["name"]))
            elif isinstance(item, str):
                raw_tags.append(item)
    labels = map_tags(raw_tags)
    return {
        "status": "hit" if labels else "miss",
        "genres": labels,
        "language": None,
        "source": "musicbrainz",
    }


def _query_lastfm(name: str, artists: Any, api_key: str, deadline: float | None) -> dict[str, Any] | None:
    if _deadline_passed(deadline):
        return None
    arts = artist_names(artists)
    artist = arts[0] if arts else ""
    if not artist:
        return {"status": "miss", "genres": [], "language": None, "source": "lastfm"}
    url = "https://ws.audioscrobbler.com/2.0/?" + urlencode(
        {
            "method": "track.getInfo",
            "api_key": api_key,
            "artist": artist,
            "track": name,
            "format": "json",
        }
    )
    try:
        data = _external_json(url, user_agent=MB_USER_AGENT)
    except RuntimeError:
        return {"status": "miss", "genres": [], "language": None, "source": "lastfm"}
    track = (data or {}).get("track") or {}
    if not track or track.get("error") or (data or {}).get("error"):
        return {"status": "miss", "genres": [], "language": None, "source": "lastfm"}
    title = track.get("name") or ""
    artist_name = ((track.get("artist") or {}) if isinstance(track.get("artist"), dict) else {}).get("name") or ""
    if not titles_match(name, title) or not artists_match(artists, [artist_name] if artist_name else []):
        return {"status": "miss", "genres": [], "language": None, "source": "lastfm"}
    tags_wrap = (track.get("toptags") or {}).get("tag") or []
    useful: list[str] = []
    counts: list[int] = []
    for item in tags_wrap:
        if not isinstance(item, dict):
            continue
        try:
            count = int(item.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        counts.append(count)
        useful.append((item.get("name") or "", count))
    max_count = max((c for _, c in useful), default=0)
    # Drop low-reach noisy tags; keep those with some agreement.
    kept = []
    for tag_name, count in useful:
        if not tag_name:
            continue
        if max_count and count < max(2, int(max_count * 0.08)):
            continue
        kept.append(tag_name)
    labels = map_tags(kept)
    return {
        "status": "hit" if labels else "miss",
        "genres": labels,
        "language": None,
        "source": "lastfm",
    }


# ---------------------------------------------------------------------------
# Enrichment orchestration
# ---------------------------------------------------------------------------

def _merge_entry(existing: dict | None, incoming: dict) -> dict:
    base = dict(existing or {})
    genres = unique_labels(list(base.get("genres") or []) + list(incoming.get("genres") or []), _GENRE_SET)
    language = incoming.get("language") or base.get("language")
    tried = list(base.get("sourcesTried") or [])
    source = incoming.get("source")
    if source and source not in tried:
        tried.append(source)
    out = {
        "genres": genres,
        "language": language,
        "status": "hit" if (genres or language) else "miss",
        "sourcesTried": tried,
    }
    for key in ("qqGenreId", "qqLanguageId"):
        if incoming.get(key) is not None:
            out[key] = incoming[key]
        elif base.get(key) is not None:
            out[key] = base[key]
    return out


def _apply_entry_to_track(track: dict, entry: dict) -> None:
    genres = unique_labels(genres_from_track(track) + _entry_genres(entry), _GENRE_SET)
    if genres:
        track["genres"] = genres
    language = language_from_track(track) or _entry_language(entry)
    if language:
        track["language"] = language


def enrich_tracks(
    tracks: list[dict],
    *,
    cache_path: Path | None = None,
    budget_s: float = DEFAULT_ENRICH_BUDGET_S,
    fetch_json: FetchJson | None = None,
    lastfm_api_key: str | None = None,
    enable_musicbrainz: bool = True,
    workers: int = DEFAULT_QQ_WORKERS,
) -> dict[str, Any]:
    """Fill missing genres/language in place when a strict match is available.

    Returns a meta dict: looked_up, cached, timed_out, newly_resolved, ...
    """
    path = Path(cache_path) if cache_path is not None else DEFAULT_CACHE_PATH
    fetcher = fetch_json or _default_qq_fetch
    key = lastfm_api_key if lastfm_api_key is not None else os.environ.get("LASTFM_API_KEY")
    deadline = time.monotonic() + max(0.0, float(budget_s))
    cache = load_cache(path)
    entries: dict[str, Any] = cache.setdefault("entries", {})
    dirty = False
    timed_out = False
    looked_up = 0
    cache_hits = 0
    skipped_deadline = 0

    pending_qq: list[tuple[int, dict, str]] = []
    pending_other: list[tuple[int, dict, str]] = []
    seen_qq: set[str] = set()
    seen_other: set[str] = set()
    key_tracks: dict[str, list[dict]] = {}

    for idx, track in enumerate(tracks):
        if not isinstance(track, dict):
            continue
        key_id = cache_key(str(track.get("name") or ""), track.get("artists"))
        if key_id:
            key_tracks.setdefault(key_id, []).append(track)
        entry = entries.get(key_id) if key_id else None
        if isinstance(entry, dict):
            _apply_entry_to_track(track, entry)
            cache_hits += 1
        if _track_has_mappable_genre(track) and language_from_track(track):
            continue
        if not key_id:
            continue
        tried = set((entry or {}).get("sourcesTried") or []) if isinstance(entry, dict) else set()
        need_genre = not _track_has_mappable_genre(track)
        need_language = language_from_track(track) is None
        if not need_genre and not need_language:
            continue
        # If the playlist already gave us a song-level QQ genre id, do not
        # search QQ again (it is the same catalogue). MusicBrainz is only a
        # second source when QQ did not hit, so unknown native IDs stay
        # unmapped rather than delaying the report.
        has_native_qq_id = _coerce_int(track.get("genreId") if "genreId" in track else track.get("genre")) is not None
        if need_genre or need_language:
            if has_native_qq_id:
                continue
            if "qq" not in tried:
                if key_id not in seen_qq:
                    seen_qq.add(key_id)
                    pending_qq.append((idx, track, key_id))
            elif key_id not in seen_other:
                seen_other.add(key_id)
                pending_other.append((idx, track, key_id))

    def write_cache() -> None:
        nonlocal dirty
        if dirty:
            save_cache(cache, path)
            dirty = False

    def store(key_id: str, incoming: dict, track: dict | None = None) -> None:
        nonlocal dirty
        with _CACHE_LOCK:
            merged = _merge_entry(entries.get(key_id) if isinstance(entries.get(key_id), dict) else None, incoming)
            entries[key_id] = merged
            dirty = True
        targets = key_tracks.get(key_id) or ([track] if track is not None else [])
        for item in targets:
            _apply_entry_to_track(item, merged)

    # --- QQ (bounded concurrency) ---
    def qq_one(item: tuple[int, dict, str]) -> None:
        nonlocal looked_up, timed_out, skipped_deadline
        idx, track, key_id = item
        if _deadline_passed(deadline):
            timed_out = True
            skipped_deadline += 1
            return
        try:
            looked_up += 1
            result = _query_qq_track(str(track.get("name") or ""), track.get("artists"), fetcher)
        except Exception:
            result = {"status": "miss", "genres": [], "language": None, "source": "qq"}
        store(key_id, result, track)

    if pending_qq:
        worker_n = max(1, int(workers))
        pending_iter = iter(pending_qq)
        in_flight: set = set()

        def submit_available() -> None:
            while len(in_flight) < worker_n:
                if _deadline_passed(deadline):
                    return
                try:
                    item = next(pending_iter)
                except StopIteration:
                    return
                in_flight.add(pool.submit(qq_one, item))

        with ThreadPoolExecutor(max_workers=worker_n) as pool:
            submit_available()
            while in_flight:
                done = next(as_completed(in_flight))
                in_flight.remove(done)
                try:
                    done.result()
                except Exception:
                    pass
                if _deadline_passed(deadline):
                    timed_out = True
                    # Let in-flight requests finish; do not start more.
                    continue
                submit_available()
        # Anything not submitted was skipped due to the time budget.
        skipped_deadline += sum(1 for _ in pending_iter)
        if skipped_deadline:
            timed_out = True
        write_cache()

    # Tracks still unidentified after QQ go to slower fallbacks.
    still: list[tuple[int, dict, str]] = []
    seen_keys: set[str] = set()
    for group in (pending_qq, pending_other):
        for idx, track, key_id in group:
            if _track_has_mappable_genre(track):
                continue
            if key_id in seen_keys:
                continue
            seen_keys.add(key_id)
            still.append((idx, track, key_id))

    if enable_musicbrainz and not _deadline_passed(deadline) and (deadline - time.monotonic()) >= 2.0:
        for idx, track, key_id in still:
            entry = entries.get(key_id) if isinstance(entries.get(key_id), dict) else {}
            tried = set((entry or {}).get("sourcesTried") or [])
            if "musicbrainz" in tried:
                continue
            if _deadline_passed(deadline):
                timed_out = True
                skipped_deadline += 1
                continue
            looked_up += 1
            result = _query_musicbrainz(str(track.get("name") or ""), track.get("artists"), deadline)
            if result is None:
                timed_out = True
                skipped_deadline += 1
                break
            store(key_id, result, track)
        write_cache()

    if key:
        for idx, track, key_id in still:
            if _track_has_mappable_genre(track):
                continue
            entry = entries.get(key_id) if isinstance(entries.get(key_id), dict) else {}
            tried = set((entry or {}).get("sourcesTried") or [])
            if "lastfm" in tried:
                continue
            if _deadline_passed(deadline):
                timed_out = True
                skipped_deadline += 1
                continue
            looked_up += 1
            result = _query_lastfm(str(track.get("name") or ""), track.get("artists"), key, deadline)
            if result is None:
                timed_out = True
                skipped_deadline += 1
                break
            store(key_id, result, track)
        write_cache()

    write_cache()
    identified = sum(1 for t in tracks if isinstance(t, dict) and _track_has_mappable_genre(t))
    return {
        "lookedUp": looked_up,
        "cacheHits": cache_hits,
        "timedOut": timed_out,
        "skipped": skipped_deadline,
        "identified": identified,
        "pending": skipped_deadline,
    }


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

def _ratio(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round(part / whole, 4)


def _bucket_stats(counts: dict[str, int], total: int) -> list[dict]:
    items = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [
        {"name": name, "count": count, "share": _ratio(count, total)}
        for name, count in items
        if count > 0
    ]


def genre_language_stats(
    tracks: list[dict],
    track_count: int,
    notes: list[str],
    *,
    enrich_meta: dict[str, Any] | None = None,
) -> tuple[dict | None, dict | None]:
    genre_counts: dict[str, int] = {name: 0 for name in CANONICAL_GENRES}
    language_counts: dict[str, int] = {name: 0 for name in CANONICAL_LANGUAGES}
    genre_covered = 0
    language_covered = 0

    for raw in tracks:
        track = raw if isinstance(raw, dict) else {}
        labels = genres_from_track(track)
        if labels:
            genre_covered += 1
            # A track may count toward more than one genre.
            for label in labels:
                genre_counts[label] = genre_counts.get(label, 0) + 1
        lang = language_from_track(track)
        if lang:
            language_covered += 1
            language_counts[lang] = language_counts.get(lang, 0) + 1

    def decide(kind: str, covered: int, counts: dict[str, int], unit: str) -> dict | None:
        if track_count <= 0 or covered <= 0:
            if track_count:
                notes.append(f"没有已识别的{unit}，已省略{kind}统计（不会猜测）。")
            return None
        coverage = covered / track_count
        notes.append(f"基于已识别{unit}的 {covered} / {track_count} 首。")
        if covered < MIN_GENRE_TRACKS or coverage < MIN_GENRE_COVERAGE:
            notes.append(
                f"仅识别出 {covered} 首的{unit}（覆盖 {round(coverage * 100)}%），"
                f"不足以展示{kind}分布。"
            )
            return None
        return {"covered": covered, "buckets": _bucket_stats(counts, track_count)}

    genres = decide("风格", genre_covered, genre_counts, "风格")
    languages = decide("语种", language_covered, language_counts, "语种")

    meta = enrich_meta or {}
    if meta.get("timedOut"):
        notes.append(
            f"本次识别了 {genre_covered} 首，其余已缓存待下次补全。"
        )
    return genres, languages
