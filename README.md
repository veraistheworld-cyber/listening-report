# 听歌报告入口 MVP

最小可用入口：粘贴公开歌单链接 → 拉取完整曲目 → 列出歌名 / 歌手 / 首数供核对 → 确认后点「开始分析」，得到规则统计报告（含风格、语种、常听的人、年代等）。人格卡本期为付费占位，不调用模型。

路径：`/workspace/playlist-report`  
只需 Python 3.10+（本机用 3.13 验证），**没有第三方依赖，不必 pip**。

## 启动

```bash
cd /workspace/playlist-report
./start.sh
```

或：

```bash
python3 app.py
```

浏览器打开 [http://127.0.0.1:8765/](http://127.0.0.1:8765/)。按 `Ctrl+C` 结束，不要让服务长期占端口。

若要在同一局域网用手机打开，加上：

```bash
./start.sh --host 0.0.0.0 --port 8765
```

## 支持范围

| 平台 | 能力 |
| --- | --- |
| 网易云音乐 | 公开歌单链接直拉，自动分页拉全 |
| QQ 音乐 | 公开歌单链接直拉，自动分页拉全 |
| 汽水音乐 | **beta**：只解析公开分享页里已嵌入的数据；可能不完整，失败会给出说明 |
| 酷狗音乐 | **不支持链接直拉**。请贴「歌名 - 歌手」文本，或先到 [music.unmeta.cn](https://music.unmeta.cn/) 转换 |
| 任意平台 | 手动粘贴曲目文本作为托底 |

识别的链接形态包括：`music.163.com/#/playlist?id=`、`/playlist?id=`、`/m/playlist`、`163cn.tv` 短链（跟随跳转）；`y.qq.com/n/ryqq/playlist/`、`ryqq_v2`、`taoge?id=`、`c6.y.qq.com` 等短链；`qishui.douyin.com/s/`、`music.douyin.com/qishui/share/playlist`。私密、已删除或需登录的歌单会返回明确错误。

文本格式：每行一首，`歌名 - 歌手`（也支持 `—`、`/`、制表符或开头序号）。

## 接口

### 拉取歌单

`POST /api/fetch-playlist`，JSON 体为 `{ "url": "..." }` 或 `{ "text": "..." }`。

成功时大致为：

```json
{
  "platform": "netease",
  "title": "热歌榜",
  "trackCount": 200,
  "tracks": [
    { "name": "海阔天空", "artists": ["Beyond"], "album": "乐与怒", "durationMs": 326000, "year": 1993, "genreId": 1, "languageId": 1 }
  ]
}
```

`platform` 可能是 `netease` / `qq` / `qishui` / `text`。汽水可能额外带 `beta`、`warning`。失败时为 `{ "error": "中文说明" }`。

QQ 音乐公开歌单接口里的歌曲对象若已带整数 `genre` / `language`，拉取时会写成 `genreId` / `languageId`（这是歌曲级编号，和歌手筛选编号不是一套，不会串用）。网易云歌曲详情没有风格字段，不会伪造。汽水若分享页里已有字符串标签，会原样带上，分析时再映射到统一曲风表。

`year` 是可选字段：仅当接口/分享页里已经带有可解析的发行年份时才会写入（合理范围 1900–2026）。不会猜测年份。常见来源：

- 网易云：`song.publishTime` / `album.publishTime`（毫秒时间戳，偶见直接是年份）
- QQ 音乐：歌曲或专辑上的 `time_public` / `publicTime` / `pub_time`（`YYYY-MM-DD`、年份数字或时间戳）
- 汽水：曲目或专辑上若已有 `year` / `release_date` / `publish_time` 一类字段，同样写入

请求体上限 4MB，以便分析接口接收完整 `tracks`。

### 分析报告

`POST /api/analyze`，把拉取结果（至少含 `tracks` 数组，通常连同 `title`、`platform`、`trackCount`、`warning`）原样提交。缺少 `tracks` 或它不是数组时返回 400 与中文错误。

成功时大致为：

```json
{
  "ok": true,
  "title": "热歌榜",
  "platform": "netease",
  "trackCount": 200,
  "stats": {
    "trackCount": 200,
    "uniqueArtists": 180,
    "uniqueAlbums": 190,
    "topArtists": [{ "name": "某歌手", "count": 6, "share": 0.03 }],
    "top3Share": 0.08,
    "collabRate": 0.21,
    "duration": {
      "covered": 200,
      "totalMs": 48000000,
      "avgMs": 240000,
      "longest": { "name": "…", "artists": ["…"], "durationMs": 320000 },
      "shortest": { "name": "…", "artists": ["…"], "durationMs": 90000 }
    },
    "eras": {
      "covered": 180,
      "buckets": [{ "label": "2020s", "count": 150, "share": 0.8333 }]
    },
    "genres": {
      "covered": 160,
      "buckets": [{ "name": "流行", "count": 80, "share": 0.4 }]
    },
    "languages": {
      "covered": 170,
      "buckets": [{ "name": "国语", "count": 120, "share": 0.6 }]
    },
    "topAlbums": [{ "name": "某专辑", "count": 3, "share": 0.015 }]
  },
  "notes": [
    "年代分布基于有发行年份的 180 首。",
    "基于已识别风格的 160 / 200 首。",
    "基于已识别语种的 170 / 200 首。"
  ],
  "personality": {
    "locked": true,
    "teaser": "基于上面的统计摘要生成一张短人格卡。本期先占位，不调用模型。"
  }
}
```

百分比约定：`share`、`top3Share`、`collabRate`、年代桶以及风格 / 语种桶的 `share` 都是 **0–1 的小数**（例如 `0.25` 表示 25%）。前端乘 100 后再展示。风格和语种的分母是歌单总首数（与歌手统计一致）；一首歌可以同时计入多个风格，因此各风格占比之和可能超过 1。年代桶的分母仍是「有发行年份的曲目数」。

统计规则（不猜测；风格在统计前会做有限补全，见下一节）：

- 曲目数以实际 `tracks` 长度为准；独立歌手数按去空后的名字去重。
- Top 歌手最多 10 名，给出曲目数与占歌单比例；合作曲会分别计入每位歌手。
- `top3Share` 是集中度：至少出现过前 3 名歌手之一的曲目比例（按曲目去重，不会因为合作而超过 1）。
- `collabRate`：清理空名后 `artists` 长度 ≥ 2 的曲目比例。
- 独立专辑数按去空专辑名去重；若有重复专辑，`topAlbums` 给出出现至少 2 次的前几张。
- 时长：只统计带有正数 `durationMs` 的曲目。给出总时长、平均、最长 / 最短（歌名、歌手、毫秒）。部分缺失时 `notes` 会说明仅基于 N 首；全部缺失则 `duration` 为 `null`，前端不展示该块。
- 年代：只看 `tracks` 里已经存在且落在 1900–2026 的 `year` 字段，按十年分桶（`1990s` / `2000s` / `2010s` / `2020s` 等），并注明「基于有发行年份的 N 首」。若有年份的曲目少于 8 首，或覆盖不足 30%，则 `eras` 为 `null`，不展示年代块，并在 `notes` 说明。绝对不会从歌名、专辑或其他字段猜年份。
- 风格 / 语种：见下节。覆盖不足（已识别 < 8 首或 < 30%）时对应字段为 `null`，但 `notes` 仍会写清「基于已识别风格（或语种）的 N / 总首」。对不上、编号未知或匹配不可靠的曲目标为未识别，不会默认成「流行」。
- `personality.locked` 恒为 `true`，文案只说明这是付费占位，不生成人格解读，也不用大模型去猜曲风。


## 风格与语种

报告里的风格一律使用我们自建的统一曲风表，不会把任何平台的原始分类直接显示给用户。一首歌可以有多个风格。

顶层标签：流行、说唱、R&B、摇滚、民谣、电子、舞曲、国风、爵士、蓝调、金属、朋克、独立/另类、轻音乐、古典、原声/影视、ACG、乡村、雷鬼、拉丁、世界音乐。外部英文 tag（Last.fm / MusicBrainz 等）会映射到这些顶层，不收录过细的微曲风。

### 来源分层

1. **歌单里已有的歌曲级字段**。QQ 公开歌单接口若已带 `genre` / `language` 整数编号，直接映射进统一表。这是歌曲级编号，不是歌手筛选编号（例如歌手筛选里 6 是嘻哈，歌曲 34 才是说唱）。网易云歌曲详情没有风格字段；歌单 tags 只代表整张歌单，不会当成每首歌的曲风。
2. **对没有可映射曲风的曲目**（网易云 / 汽水 / 文本，或 QQ `genre=0` / 未知编号），用「歌名 + 歌手」走已经跑通、无需自制签名的公开 QQ 搜索 + 单曲详情：
   - `https://c.y.qq.com/soso/fcgi-bin/client_search_cp?format=json&p=1&n=5&w=歌名+歌手`（Referer: `https://y.qq.com/`）
   - `https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg?tpl=yqq_song_detail&format=json&songmid=MID`（同一 Referer）
   - 匹配必须严格：标题规范化后相同（可忽略括号里 Live / Remix 等版本属性，但不会匹成另一首歌），且至少一位歌手名规范化后相同。不符合就丢弃这次补全。
3. **QQ 未命中时**，若时间预算还够，再试 MusicBrainz（免 Key，带有意义的 User-Agent，严格 1 次/秒）。Last.fm 需要 Key，只在环境变量 `LASTFM_API_KEY` 存在时才调用，否则跳过。
4. 大模型不参与曲风判断。

查到的结果（命中、未命中、无结果）都会写入 `data/genre-cache.json`（相对项目目录，原子写入）。键为规范化后的 `name|artist`。第二次分析同一歌单应主要走缓存。补全使用有限并发（默认 4）和整次时限（默认约 22 秒）；超时未查到的标为未识别，并在 `notes` 说明「本次识别了 N 首，其余已缓存待下次补全」。

`data/genre-cache.json` 已加入 `data/.gitignore`，不要把它打进发布包。

已核实并写入映射的 QQ 歌曲级 `genre` ID（2026-11-12，公开搜索 + 详情交叉验证）：`1` 流行，`15` 蓝调，`19` 乡村，`20` 舞曲，`22` 电子，`23` 民谣，`33` R&B，`34` 说唱，`35` 雷鬼，`36` 摇滚，`37` 原声/影视。`0` 视为无曲风（未识别）。出现过但核不清、因而**未映射**的编号包括：`3`（爵士作品与贝多芬《Für Elise》共用）、`27`（拉丁与雷鬼混用）、`28`、`39`（不足以称为「世界音乐」）、`41`、`50`（bad guy / Numb / Creep 等，像独立/另类但不稳定）、`51`（久石让《いつも何度でも》与 LiSA《紅蓮華》，不能稳定归为 ACG 或原声）等。没有完整官方表，就只保留已核实的 ID，绝不把未知 ID 默认成流行。

已核实的 QQ `language` ID：`0` 国语，`1` 粤语，`2` 闽南语，`3` 日语，`4` 韩语，`5` 英语，`9` 纯音乐（这是语种字段里的属性，交叉验证于石进《夜的钢琴曲》、贝多芬纯音乐版、影视器乐主题等；不确认的编号不当成语种），`16` 西班牙语。`7` 等其余编号未映射。

MusicBrainz 的曲风数据若用于**商业**产品需要另行授权。本期是本地学习 / MVP，真要上线需另谈。

本地测试：`python3 test_analyze.py` 与 `python3 test_genres.py`（stdlib unittest，无需装包）。

## 测过的示例链接（2026-11-10，Asia/Shanghai）

在本机对真实公开歌单跑通，验证后已关掉服务。

**网易云 · 热歌榜**（官方榜，一次返回完整列表）

- 链接：`https://music.163.com/#/playlist?id=3778678`
- 请求：`POST /api/fetch-playlist` `{"url":"https://music.163.com/#/playlist?id=3778678"}`
- 结果：HTTP 200，`platform=netease`，标题「热歌榜」，**200 首**；首曲《无 意》（加木 / Demxntia），约 2.5 秒

**网易云 · 一人一首成名曲【华语篇】**（用户歌单，需按 trackIds 分页拉全）

- 链接：`https://music.163.com/playlist?id=83169609`
- 结果：HTTP 200，标题「一人一首成名曲【华语篇】」，**388 首**全部拉到，约 6.0 秒

**QQ 音乐 · 甜度爆表 | 旋律说唱狙击少女心**

- 链接：`https://y.qq.com/n/ryqq/playlist/7707261125`
- 请求：`POST /api/fetch-playlist` `{"url":"https://y.qq.com/n/ryqq/playlist/7707261125"}`
- 结果：HTTP 200，`platform=qq`，标题「甜度爆表 | 旋律说唱狙击少女心」，**66 首**；首曲《你的》，约 1.8 秒

**QQ 音乐 · 较大歌单（验证拉全）**

- 链接：`https://y.qq.com/n/ryqq_v2/playlist/7729596131`
- 结果：HTTP 200，标题「耳机里的秘密 | 宝藏女声集合站」，**1292 首**全部拉到，约 4.4 秒

**汽水（beta，分享页可能截断）**

- 链接：`https://music.douyin.com/qishui/share/playlist?playlist_id=7205556248284053541&sec_sharer_id=MS4wLjABAAAARhE2M2aH4VBTM6wO7ah7fEgWfYX-6mjHzI5gFhESQ-w`
- 结果：HTTP 200，标题「练！」，分享页只嵌入 **72 / 91** 首，接口带 `warning` 与 `beta: true`，未伪装成已拉全

也测过：无效网易云 / QQ 歌单会明确报错；酷狗链接返回引导改贴文本或先转换的错误；文本托底「海阔天空 - Beyond」两条可解析。

本地快速复测示例：

```bash
./start.sh &
curl -sS -X POST http://127.0.0.1:8765/api/fetch-playlist \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://music.163.com/#/playlist?id=3778678"}'
# 测完后结束后台进程，不要长期占 8765 端口
```

拉取后再把完整 JSON 交给 `POST /api/analyze` 即可得到统计报告。

## 当前限制

- 人格卡仍是付费占位，不会调用模型，也不会编造人格文案或曲风。
- 年代分布完全依赖拉取结果里已有的 `year`。若某平台当次响应里没有可解析的发行年份，报告会省略年代块，而不是补造年份。
- 风格不会猜测：未知 QQ 编号、匹配不严格或补全超时的曲目都记为未识别。覆盖不足时不展示风格 / 语种块，但仍在 `notes` 写清覆盖率。
- 酷狗没有做链接直拉（缺少可稳定、公开依赖、且无需自制签名的歌单接口）。
- 汽水只读公开分享页里已经写进去的 JSON；较大歌单常常不完整，请当 beta 使用，完整列表改走文本。
- 私密歌单、失效链接、单曲分享链接（例如汽水 `/share/track`）无法当歌单拉取。
- 没有申请平台开发者 Key。本期只用对公众开放、无需自制签名的查询接口，以及分享页里明确嵌入的数据。平台改接口后可能失效；若以后要走官方开放平台，把 Key 放到环境变量即可（本期未预留实际调用）。
- 没有实现 weapi / eapi 破解、加密签名绕过或仿客户端秘钥。
- 超大网易云歌单会分批请求曲目详情，可能要等十几秒。

## 目录

```
playlist-report/
  app.py            # 本地 HTTP 服务（stdlib）
  fetchers.py       # 链接识别与拉取（含可选 year / genreId / languageId）
  analyze.py        # 规则统计报告引擎
  genres.py         # 统一曲风表、映射、补全与缓存
  test_analyze.py   # stdlib unittest（统计）
  test_genres.py    # stdlib unittest（曲风映射 / 匹配 / 缓存）
  start.sh
  README.md
  data/             # 运行期缓存（genre-cache.json 不入库）
  static/           # 中文单页（夜间 / 琴房气质）
```
