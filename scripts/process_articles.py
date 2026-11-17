"""
公众号福利推文处理脚本：去重、分类、官方判定、提取结构化字段。

用法：
    python scripts/process_articles.py \
        --input data/raw_articles.json \
        --output data/articles.json

跨日去重（v3）：
    只跳过「今天之前」推荐过的内容；同一天内多轮采集不去重，
    当天早上推荐过的内容午间/晚间仍会展示，避免漏看。
    --history-file data/history.json   默认自动加载和维护
    --no-history                       跳过历史去重（强制重跑某天内容）
    --reset-history                    清空历史后从头开始（慎用）
    --retention-days 30                历史保留天数（默认 30）
    注：上游已标注的 is_official 字段优先，未标注才用本地规则推断。
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from datetime import datetime, date, timedelta

# 活动级去重（同目录模块：同一活动换个标题/链接也判为重复）
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from entity_dedupe import is_same_activity, explain as _explain_entity, title_sim as _title_sim
except ImportError:  # 模块缺失时降级为只做指纹去重
    is_same_activity = None
    _explain_entity = None
    _title_sim = None

# 来源账号过滤（官方号 vs 福利整理号）
try:
    from source_filter import classify_source as _classify_source, should_drop as _source_should_drop
except ImportError:  # 模块缺失时不做来源过滤
    _classify_source = None
    _source_should_drop = None


# 分类规则：关键词 -> 分类
# 顺序即优先级：越靠前越优先命中。
CATEGORY_RULES = [
    # 送礼类：实物礼品 / 赠品 / 周边 / 试用装 / 免单（抽中即得的「物」）
    # 「好礼/文创/周边」必须带动词前缀（送/赢/抽/领/兑），否则会被无关表述误抢：
    #   · 「万元享好礼」虚指 → 把立减金达标活动抢进送礼桶（2026-10-13 邮储案例）
    #   · 「德阳及周边消费人群」地理表述 → 抢走消费券（2026-10-15 绵竹案例）
    #   · 「全场图书、文创通用」券适用范围 → 抢走书展惠民券（2026-10-15 案例）
    ("gift",       re.compile(r"(礼品|礼物|赠品|(?:送|赢|抽|领|兑)(?:好礼|文创|周边)|"
                              r"手办|挂件|保温杯|礼盒|小样|试用装|"
                              r"实物奖|盲盒|纪念品|免单|免费喝|免费领|包邮送)", re.I)),
    # 票根类走 HEADLINE_ONLY_RULES（只看标题），此处不再重复定义
    # 「红包卡券」「红包卡包」是美团/点评的卡包路径名，不是红包活动 → 用负向断言排除
    # 「到账」收窄：券/票「到账」不是红包，只有「红包/奖励/现金/返现到账」才算
    ("red_packet", re.compile(r"(红包(?!卡券|卡包)|现金(?!券)|补贴|津贴|返现|(?:红包|奖励|现金|立减金)到账|立减金)", re.I)),
    # 券类先于 welfare：「免费/送」这类泛词会把「消费券…免费领」误抢到福利桶
    ("discount",   re.compile(r"(优惠|折扣|立减|满减|秒杀|特价|降价|促销|消费券|买一送一|买一赠一|第二杯|满\d+元?减\d+)", re.I)),
    ("lottery",    re.compile(r"(抽奖|抽签|抽取|抽选|开奖|中奖|有奖|竞答|竞赛|答题|获奖|点赞)", re.I)),
    # 泛词「券」兜底，且**必须排在 lottery 之后**：
    # 抽奖活动的奖品常是「住宿券/免单券」，泛词在前会把抽奖误归成优惠
    # （2026-10-15 珠海「小布有礼周周送」案例）。
    # 作用：兜住「30元无门槛券」「购书券」这类既无「满减」也无「优惠」的表述。
    ("discount",   re.compile(r"券", re.I)),
    ("welfare",    re.compile(r"(福利|赠送|免费|会员|特权|礼包|领取|送)", re.I)),
]

# 官方/品牌判定信号（标题/摘要）
OFFICIAL_SIGNALS = re.compile(
    r"(官方|旗舰|品牌|认证|订阅号|服务号|公众号)",
    re.I,
)

# 官方判定信号（来源字段）：政府机关 / 工会 / 协会 / 银行等
SOURCE_OFFICIAL_SIGNALS = re.compile(
    r"(官方|政府|局$|厅$|部$|委员会|工会|协会|学会|中心|集团|银行|研究院)",
    re.I,
)

HISTORY_SCHEMA_VERSION = 1


# ---------------- 过期判定 ----------------

# 结束日期话术：「持续至10月31日」「活动时间：10月1日至12月31日」「截止10月7日」
# 「10.1-12.31」「10月1日—12月31日」这种区间写法取右端作结束日
# ⚠️ 2026-10-21 修复：金额区间被误读成日期区间，导致活动被误判过期。
# 案例①：银行线报里「抽1.08-6.8元」被 pattern-3 读成 6月8日 → 10月21日当天的新线报被判过期剔除。
# 案例②：「0.3元至1.8元随机红包」会被 pattern-1 读成 1月8日。
# 解法：日期匹配结尾禁止紧跟 元/折/%/倍/分/块 等金额单位，
#       且月份必须落在 1-12（0 月、13 月以上一律不是日期）。
_MONEY_TAIL = r"(?!\s*(?:元|折|%|％|倍|分|块|角|块钱|个点))"
# ⚠️ 2026-10-22 二次修复：光挡金额单位还不够——正则会**回溯**绕过去。
# 「至5.88元」先试 (\d{1,2})="88" 被 _MONEY_TAIL 挡掉，回溯成 "8" 后紧跟的是数字 8，
# 于是匹配到「至5.8」→ 5月8日 → 10月22日的银行线报又被判过期。
# 追加 (?!\d)：日期末尾不允许紧跟数字，彻底封死小数金额区间。
_NOT_DIGIT_TAIL = r"(?!\d)"
_END_DATE_PATTERNS = [
    # ⚠️ 2026-10-22 三次修复：`\s*` 挡不住全角冒号，「截止时间：2026年10月1日」整条匹配不上
    # → 已结束的活动抽不到结束日 → 当未过期放行。改为允许任意「时间/日期」后跟冒号与空白。
    re.compile(r"(?:持续至|发放至|截止(?:时间|日期)?(?:为|是)?[:：]?|有效期至|至)\s*"
               r"(\d{4})?[年\-]?(\d{1,2})[月\-\.](\d{1,2})日?" + _MONEY_TAIL + _NOT_DIGIT_TAIL),
    re.compile(r"(\d{1,2})月(\d{1,2})日(?:\s*\d{1,2}[:：]\d{2}(?::\d{2})?)?\s*(?:截止|结束|止)" + _NOT_DIGIT_TAIL),
    # 区间右端：10.1-12.31 / 10月1日—12月31日 / 9月30日至10月31日
    re.compile(r"\d{1,2}(?:月|\.)\d{1,2}日?\s*(?:至|到|—|―|–|-|~|～)\s*"
               r"(?:(\d{4})年)?(\d{1,2})(?:月|\.)(\d{1,2})日?" + _MONEY_TAIL + _NOT_DIGIT_TAIL),
    # 「11.5截止」这类点号缩写：线报里极常见，但原三条要求带「月」字，全部漏掉
    # → 最后一天的活动抽不到结束日，被当成「无期限」放行（2026-11-05 新增）。
    re.compile(r"(?<![\d.])(\d{1,2})[.\-](\d{1,2})日?\s*(?:截止|结束|止)"
               + _MONEY_TAIL + _NOT_DIGIT_TAIL),
]


def extract_end_date(text: str) -> date | None:
    """从标题/摘要里抽取「活动结束日期」，抽不到返回 None。

    只认明确的「至/截止/结束」话术，避免把发布日期误判成结束日期。
    """
    if not text:
        return None
    for pat in _END_DATE_PATTERNS:
        for m in pat.finditer(text):
            g = m.groups()
            try:
                if len(g) == 3:
                    y = int(g[0]) if g[0] else date.today().year
                    mo, d = int(g[1]), int(g[2])
                else:
                    y, mo, d = date.today().year, int(g[0]), int(g[1])
                return date(y, mo, d)
            except (ValueError, TypeError):
                continue
    return None


def is_expired(article: dict, today: date | None = None) -> bool:
    """判断活动是否已过期（结束日期早于今天）。无明确结束日期时返回 False。"""
    today = today or date.today()
    text = f"{article.get('title', '')} {article.get('summary', '')}"
    end = extract_end_date(text)
    return bool(end and end < today)


# ⚠️ 2026-11-05 新增口径（用户原话：「最后一天的不提供，无特殊需求」）：
# 只剩今天一天的活动不再提供。理由：看到时往往来不及参与，
# 或者奖品/名额当天已被抢完，推出去等于让用户白跑一趟。
# 除「结束日期 == 今天」外，还要认这句纯文字写法——线报里最常见的就是
# 「（今天最后一天）」「今日24:00截止」，这类文本往往不带可解析的日期。
_RE_LAST_DAY_TEXT = re.compile(
    # 「明天最后一天」是说明天才是截止日，不是今天——前缀必须排除明天/明日/后天，
    # 否则建信基金（11.6 截止、摘要写「明天最后一天」）会被误判成今天到期而剔掉。
    r"(?<!不是)(?<!绝非)(?<!不算)(?<!明天)(?<!明日)(?<!后天)(?<!次日)最后一天"
    r"|(?:今天|今日|当天)\s*\d{0,2}\s*[点时:：]?\s*\d{0,2}\s*分?\s*(?:截止|结束|到期|关闸)"
)


def is_last_day(article: dict, today: date | None = None) -> bool:
    """判断活动是否「今天就是最后一天」。无明确结束日期时返回 False。

    与 is_expired 分开统计：不是过期，而是「来不及」。
    """
    today = today or date.today()
    text = f"{article.get('title', '')} {article.get('summary', '')}"
    end = extract_end_date(text)
    if end and end == today:
        return True
    return bool(_RE_LAST_DAY_TEXT.search(text))


# ---------------- 分类 & 去重 ----------------

# 仅看标题的强特征分类：这类关键词只要出现在**标题**里就一定是该桶，
# 出现在 summary 里往往只是顺带一提。
# 例：成都电影消费券的摘要末尾写了「购票后可参与四川票根惠民抽奖」——
# 那是电影券活动，不是票根活动（2026-10-13 实测误分）。
HEADLINE_ONLY_RULES = [
    # 票根经济 / 凭证换折扣：标题里出现即归票根桶
    ("ticket",  re.compile(r"(票根|票享天府|票根惠民|索票|票根经济)", re.I)),
    # 抽奖 / 答题类强特征（用户 2026-10-13：「关键词应该是抽奖」）：
    # 「有奖发票 / 发票抽奖 / 消费满额抽奖」的本体就是抽奖；
    # 「XX知识竞答 / 有奖问答 / 闯关答题」的本体是答题——两者同归抽奖答题桶。
    # 必须排在 red_packet 之前：否则「答题抽微信红包」「竞答抽现金红包」
    # 会被 summary 里的「红包/现金」抢进红包桶（2026-10-19 实测 4 条全被抢）。
    ("lottery", re.compile(r"(抽奖|发票抽奖|有奖发票|定时抽奖|抽大奖|抽帕萨特|抽汽车|抽取大奖|"
                           r"答题|竞答|竞赛|问答|闯关)", re.I)),
    # 消费券强特征：标题写明「消费券」的归优惠桶。
    # 必须排在 red_packet 之前：否则「重阳敬老消费券…最高省2000元」「汽车消费券…最高补1万」
    # 会被 summary 里的「补贴」抢进红包桶（2026-10-19 实测 2 条）。
    ("discount", re.compile(r"消费券", re.I)),
    # 红包类强特征：标题写明「立减金 / 数币 / 返现」的优先归红包桶——
    # 否则「农行…抽10元立减金」会被 summary 里别的活动名（如「周周领好礼」）
    # 抢进送礼桶（2026-10-15 农行案例）
    # ⚠️ 2026-10-28 补「无门槛红包/红包口令/刷卡指南/银行羊毛/羊毛汇总」：
    # 「10月28日刷卡指南…京东每日3次无门槛红包…周三10点外卖免单30元」标题无「立减金」，
    # 掉进 CATEGORY_RULES 后被 gift 的「免单」抢走，整篇银行线报被归成送礼。
    # ⚠️ 2026-10-30 再补两处：
    # ①「羊毛汇总」→「羊毛(活动)?汇总」：10/30 标题是「羊毛活动汇总」，中间多了「活动」，
    #    被当成没命中，整篇又掉回 gift 桶（与 10/28 同一个坑的变体）。
    # ② 加「刷卡金」：银行线报标题常写「抽刷卡金」而不写「立减金」，同属支付返现类。
    ("red_packet", re.compile(r"(立减金|刷卡金|数币|数字人民币|返现|无门槛红包|红包口令|"
                              r"刷卡指南|银行羊毛|羊毛(?:活动)?汇总)", re.I)),
]


def classify(title: str, summary: str = "") -> str:
    """根据标题和摘要把文章分到主题桶。多分类匹配时按规则优先级取第一个。"""
    for cat, pat in HEADLINE_ONLY_RULES:
        if pat.search(title):
            return cat
    text = f"{title} {summary}".strip()
    for cat, pat in CATEGORY_RULES:
        if pat.search(text):
            return cat
    return "other"


def is_official(title: str, summary: str, source: str) -> bool:
    """判定是否官方/品牌发布。命中标题摘要信号，或来源为政府/工会/协会等即返回 True。"""
    text = f"{title} {summary}"
    if OFFICIAL_SIGNALS.search(text):
        return True
    return bool(SOURCE_OFFICIAL_SIGNALS.search(source or ""))


# 只剔除「跟踪/分享噪音」参数；**保留文章定位参数**（?id= / ?docid= / ?p= 等）。
# 历史坑（2026-10-15 复现）：旧实现直接 url.split("?")[0]，把
# https://www.scpublic.cn/news/getNewsDatail?id=884108（天府书展）砍成
# https://www.scpublic.cn/news/getNewsDatail，与 10/09「云购蓉城」那篇撞指纹，
# 导致全新活动被历史库误杀。同一个坑此前在 export_urls.py 里以
# so.html5.qq.com 的形式出现过一次，这里一次性从归一化层根治。
_TRACKING_PARAM_RE = re.compile(
    r"^(utm_[a-z_]+|from|spm|share[a-z_]*|sharer_[a-z_]+|scene|subscene|"
    r"clicktime|enterid|ascene|devicetype|nettype|exportkey|pass_ticket|"
    r"wx_header|uin|chksm|srcid|sessionid|lang|countrycode|ref|referrer|"
    r"source|channel|timestamp|random|nwr_flag)$",
    re.I,
)


def normalize_url(url: str) -> str:
    """去掉 url 中的 utm/share 等噪音参数，统一作为去重键。

    保留 query 里的文章定位参数（id/docid/p/sn/mid/__biz/...），
    否则同一站内不同文章会被并成同一个指纹而互相误杀。
    """
    if not url:
        return ""
    url = url.strip()
    base, _, _frag = url.partition("#")
    base, sep, query = base.partition("?")
    base = base.strip()
    if sep and query:
        keep = []
        for kv in query.split("&"):
            if not kv:
                continue
            key = kv.split("=", 1)[0].strip()
            if not key or _TRACKING_PARAM_RE.match(key):
                continue
            keep.append(kv)
        if keep:
            base = f"{base}?{'&'.join(sorted(keep))}"
    return base


# 低区分度 URL：去掉 query 后所有文章都长一样（搜索落地页/聚合页），
# 用它们做去重键会把「不同文章」误判成「同一篇」而被历史库误杀。
# 例：https://so.html5.qq.com/page/real/search_news?docid=xxx —— 真正的区分信息全在 query 里。
LOW_SIGNAL_URL = re.compile(
    r"(so\.html5\.qq\.com|/search_news|weixin\.sogou\.com|"
    r"baidu\.com/s\b|google\.com/search|sogou\.com/web|bing\.com/search)",
    re.I,
)

# 旧式指纹（历史库迁移前写入的「去 query」版本）命中时，要求标题相似度
# 达到此阈值才判定为同一篇。历史库早期条目已丢失原始 query，
# 只靠指纹会把 scpublic.cn 这类「同基址不同 id」的两篇文章误判为重复
# （2026-10-15：天府书展惠民券被 10/09「云购蓉城」误杀）。
LEGACY_TITLE_SIM = 0.75


def dedupe_key(article: dict) -> str:
    """去重键：先 URL（去 query），退化到标题。

    命中「低区分度 URL」（搜索落地页等）时直接退化到标题指纹，
    否则去 query 后的 URL 会被多篇不同文章共用，导致被历史库误判为已推荐。
    """
    url = normalize_url(article.get("url", ""))
    if url and not LOW_SIGNAL_URL.search(url):
        return f"url::{url}"
    return f"title::{article.get('title', '').strip().lower()}"


def is_duplicate_of_history(norm: dict, history_fingerprints: dict) -> tuple:
    """判断该条目是否历史已推荐过，返回 (是否重复, 命中的指纹)。

    两级匹配：
      ① 现行指纹（URL 保留 id/docid 等定位参数）——命中即判重复；
      ② 旧式指纹（去 query 的 URL）——还需标题相似度 ≥ LEGACY_TITLE_SIM。

    历史库早期条目按旧规则写入（已砍掉 query，无法反推原始链接），
    所以第 ② 级只用于「兜底拦重推」，靠标题把同站不同文章区分开，
    避免误杀（2026-10-15 天府书展惠民券案例）。
    """
    key = dedupe_key(norm)
    if key in history_fingerprints:
        return True, key

    url = (norm.get("url") or "").strip()
    if not url:
        return False, None

    legacy = f"url::{url.split('?')[0].split('#')[0].strip()}"
    titles = history_fingerprints.get(legacy)
    if not titles:
        return False, None

    if _title_sim is None:
        return False, None

    title = norm.get("title", "")
    if max(_title_sim(title, t) for t in titles) >= LEGACY_TITLE_SIM:
        return True, legacy
    return False, None


def normalize_article(raw: dict) -> dict:
    """统一字段、补默认值。下划线开头的自定义字段（如 _region_bucket）原样保留。"""
    norm = {
        "title": (raw.get("title") or "").strip(),
        "url": (raw.get("url") or "").strip(),
        "source": (raw.get("source") or raw.get("gzh") or "未知来源").strip(),
        "publish_date": raw.get("publish_date") or raw.get("date") or "",
        "summary": (raw.get("summary") or raw.get("desc") or "").strip(),
    }
    for k, v in raw.items():
        if k.startswith("_") and k not in norm:
            norm[k] = v
    # 上游已标注 is_official 时优先信任，并打标记供 process 使用
    if "is_official" in raw and raw["is_official"] is not None:
        norm["is_official"] = bool(raw["is_official"])
        norm["_official_from_upstream"] = True
    return norm


# ---------------- 跨日历史库 ----------------

def load_history(path: Path) -> dict:
    """加载历史库，文件不存在返回空；损坏则备份 .bak 并从空开始。"""
    if not path.exists():
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "retention_days": 30,
            "last_updated": None,
            "items": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or "items" not in data:
            raise ValueError("not a valid history file")
        return data
    except (json.JSONDecodeError, ValueError) as e:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        print(f"[history] 文件损坏，已备份到 {backup}，重新从空集合开始: {e}")
        return {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "retention_days": 30,
            "last_updated": None,
            "items": {},
        }


def save_history(path: Path, data: dict) -> None:
    """原子写入历史库（先写 .tmp 再 rename，避免半写）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def trim_history(history: dict, today: date, retention_days: int) -> int:
    """清理超过 retention_days 没出现过的指纹，返回清理条数。"""
    cutoff = (today - timedelta(days=retention_days)).isoformat()
    keep, removed = {}, 0
    for fp, meta in history["items"].items():
        last = meta.get("last_seen", "")
        if last and last < cutoff:
            removed += 1
            continue
        keep[fp] = meta
    history["items"] = keep
    history["retention_days"] = retention_days
    return removed


def update_history(history: dict, articles: list, today_str: str, mark_delivered: bool = True) -> int:
    """把今天保留下来的文章指纹写入历史库，返回新增/更新的条数。

    mark_delivered=True（默认）：把本次入库的条目标记为「今天已交付过」，
    仅在开启 --skip-delivered-today 时才用得上（默认同日全量呈现，不滤今日已交付）。
    """
    touched = 0
    for a in articles:
        fp = dedupe_key(a)
        if fp in history["items"]:
            # 已存在，更新 last_seen & seen_dates
            meta = history["items"][fp]
            meta["last_seen"] = today_str
            meta.setdefault("first_seen", today_str)
            dates = meta.setdefault("seen_dates", [today_str])
            if today_str not in dates:
                dates.append(today_str)
            meta["category"] = a.get("category", meta.get("category"))
            meta["title"] = a.get("title", meta.get("title"))
        else:
            history["items"][fp] = {
                "title": a.get("title", ""),
                "first_seen": today_str,
                "last_seen": today_str,
                "seen_dates": [today_str],
                "category": a.get("category"),
            }
            touched += 1
        if mark_delivered:
            history["items"][fp]["delivered"] = True
            history["items"][fp]["delivered_on"] = today_str
    history["last_updated"] = datetime.now().isoformat(timespec="seconds")
    return touched


def build_history_fingerprints(history: dict, today_str: str, skip_delivered_today: bool = False) -> dict:
    """构造需要跳过的指纹表：{指纹: [该指纹下的历史标题, ...]}。

    默认（skip_delivered_today=False）**只跳过「今天之前」推荐过的**——
    同一天内多轮采集时，当天内容会**全量重新呈现**（用户 2026-10-09 要求：
    「同日内容同样呈现」），保证用户在任何一轮都能看到今天整理过的完整内容。

    跨天一律过滤：`last_seen != today` 即跳过（铁律：推过的内容不再推）。

    传 True 可切回「今日已交付去重」：`delivered_on == today` 的条目也跳过，
    只呈现本轮新增（用于补搜轮，避免重复展示）。

    ✦ 返回**带标题的映射**而非纯 set：历史库里存在迁移前写入的「去 query」
      旧指纹，只靠指纹精确匹配会误杀同站不同文章（2026-10-15 天府书展
      vs 云购蓉城 案例），所以判重时要连标题一起校验。
    """
    skip: dict = {}
    for fp, meta in history["items"].items():
        last = meta.get("last_seen", "")
        take = (last and last != today_str) or \
               (skip_delivered_today and meta.get("delivered_on") == today_str)
        if take:
            title = (meta.get("title") or "").strip()
            if title:
                skip.setdefault(fp, []).append(title)
            else:
                skip.setdefault(fp, [])
    return skip


def build_history_activities(history: dict, today_str: str) -> list:
    """构造「今天之前推荐过的活动」列表，供活动级去重比对。

    用户硬规则（2026-09-24）：推荐过的活动不要再推荐，**不管活动有没有结束**。
    同一个活动常被不同媒体写成不同标题、挂在不同域名上，
    URL/标题精确指纹抓不到，所以这里按「主地域 + 活动类型」做语义判重。
    """
    return [
        (meta.get("title", ""), meta.get("first_seen", ""))
        for meta in history["items"].values()
        if meta.get("title") and meta.get("first_seen", "") < today_str
    ]


# ---------------- 主流程 ----------------

def process(raw_articles: list, history_fingerprints: set = None,
            today_str: str = None, history_activities: list = None,
            keep_expired: bool = False, keep_last_day: bool = False,
            keep_aggregator: bool = False) -> dict:
    """主流程：归一化 → 来源过滤 → 过期剔除 → 最后一天剔除 → 历史去重
    → 活动级去重 → 当日去重 → 分类 → 判定官方。

    来源过滤（默认开启，用户 2026-11-05 要求「只要官方账号，不要福利整理账号」）：
    账号名命中羊毛号/线报号/本地宝等整理号特征的条目直接丢弃，
    只计入 stats['dropped_aggregator']，不进入任何交付物。

    过期剔除（默认开启，用户 2026-10-09 要求）：结束日期早于今天的条目直接丢弃，
    不再出现在仪表盘里，只计入 stats['dropped_expired']。

    最后一天剔除（默认开启，用户 2026-11-05 要求「最后一天的不提供」）：
    结束日期就是今天的条目同样不展示，只计入 stats['dropped_last_day']。
    """
    # ⚠️ 不能用 `or set()`：空 dict 是 falsy，会被替换成 set，随后
    # is_duplicate_of_history 里的 .get() 直接抛 AttributeError。
    if history_fingerprints is None:
        history_fingerprints = {}
    history_activities = history_activities or []
    today_str = today_str or date.today().isoformat()

    seen = set()
    cleaned = []
    stats = {
        "total": len(raw_articles),
        "deduped_in_session": 0,
        "deduped_from_history": 0,
        "deduped_same_activity": 0,
        "dropped_aggregator": 0,
        "dropped_shared_url": 0,
        "dropped_expired": 0,
        "dropped_last_day": 0,
        "aggregator_hits": [],
        "shared_url_titles": [],
        "expired_titles": [],
        "last_day_titles": [],
        "same_activity_hits": [],
        "by_category": {},
        "by_source_kind": {"official": 0, "unknown": 0, "aggregator": 0},
    }

    # 0-pre 拼盘拆条检测（2026-11-05 下午案例）：一篇线报拼盘文被拆成 N 个
    # 「标题各异、URL 相同」的条目——当日去重用 URL+标题键拦不住它们，
    # 逐条看都是"单一活动"，但链接点开全是同一篇拼盘。这里按 URL 反向
    # 聚合：同一 URL 挂了 ≥2 个不同标题 → 全部按拼盘拆条剔除。
    url_title_map = {}
    for raw in raw_articles:
        t = (raw.get("title") or "").strip()
        u = (raw.get("url") or "").strip()
        if not t or not u:
            continue
        key = normalize_url(u)
        if key and not LOW_SIGNAL_URL.search(key):
            url_title_map.setdefault(key, set()).add(t)
    shared_urls = {u for u, titles in url_title_map.items() if len(titles) >= 2}

    for raw in raw_articles:
        norm = normalize_article(raw)
        if not norm["title"]:
            continue

        if shared_urls:
            _u = normalize_url(norm["url"])
            if _u in shared_urls and norm["url"]:
                # 2026-11-06 用户口径收窄：「拼盘拆条剔除」的本意是砍掉
                # 「羊毛汇总」这种**没有官方信息佐证**的薅羊毛稿件（账号是整理号）。
                # 官方号自己发的一篇文章里含多个活动（如政府综合推文）可以拆条保留。
                _kind = _classify_source(norm["source"])[0] if _classify_source else "aggregator"
                if _kind == "aggregator":
                    stats["dropped_shared_url"] += 1
                    stats["shared_url_titles"].append({
                        "title": norm["title"],
                        "url": norm["url"],
                        "reason": "整理号同一 URL 挂多个活动标题 = 无官方佐证的羊毛汇总拆条",
                    })
                    continue

        # 0. 来源过滤：只砍「福利整理号 + 汇总拼盘」
        #    用户 2026-11-05 口径：羊毛号/线报号/本地宝发的
        #    「多活动汇总 / 今日到期N连 / 各银行APP 拼盘」一律不进交付物；
        #    但它们转述的**单一**官方活动保留（主体、规则、链接都明确）。
        if _classify_source is not None:
            kind, acct, why = _classify_source(norm["source"])
            norm["source_account"] = acct
            norm["source_kind"] = kind
            if not keep_aggregator and _source_should_drop is not None:
                drop, drop_why, _acct = _source_should_drop(
                    norm["title"], norm["summary"], norm["source"])
                if drop:
                    stats["dropped_aggregator"] += 1
                    stats["aggregator_hits"].append({
                        "title": norm["title"],
                        "source": norm["source"],
                        "account": acct,
                        "reason": drop_why,
                    })
                    continue

        # 0.1 过期过滤：结束日期早于今天的，一律不展示（用户 2026-10-09 要求）
        if not keep_expired and is_expired(norm):
            stats["dropped_expired"] += 1
            stats["expired_titles"].append(norm["title"])
            continue

        # 0.2 最后一天过滤：今天就是截止日的，同样不提供（用户 2026-11-05 要求）
        if not keep_last_day and is_last_day(norm):
            stats["dropped_last_day"] += 1
            stats["last_day_titles"].append(norm["title"])
            continue

        key = dedupe_key(norm)

        # 用户点名拉回（2026-11-06 新增）：raw_articles.json 里标 `_force_include: true`
        # 的条目跳过跨日/活动级去重——用户明确说「可拉回来」的往期或同期活动，
        # 优先级高于「已提交内容不提供」铁律。过期/最后一天过滤不受此影响。
        force = bool(norm.pop("_force_include", False))

        # 1. 历史库去重（跨日）——两级匹配：现行指纹优先，旧式指纹需标题校验
        dup, _matched_fp = is_duplicate_of_history(norm, history_fingerprints)
        if dup and not force:
            stats["deduped_from_history"] += 1
            continue

        # 1.5 活动级去重：同一活动换个标题/链接就不算新内容（用户硬规则）
        if not force and is_same_activity is not None and history_activities:
            hit = None
            for hist_title, hist_first_seen in history_activities:
                if is_same_activity(norm["title"], hist_title):
                    hit = (hist_title, hist_first_seen)
                    break
            if hit:
                stats["deduped_same_activity"] += 1
                stats["same_activity_hits"].append({
                    "title": norm["title"],
                    "matched": hit[0],
                    "first_seen": hit[1],
                })
                continue

        # 2. 当日 session 内去重
        #    ⚠️ 2026-11-05 修复：原实现按纯 URL 键判重，导致「一篇线报里汇总了 N 个不同活动」
        #    时只留下第一篇，其余全部静默丢弃（当日 11 篇凭空消失、不报错）。
        #    改为 URL + 标题：同一文章重复出现仍能去重，聚合文里的不同活动则各自保留。
        #    跨日去重仍走 dedupe_key（URL 级），不受影响。
        session_key = f"{key}｜{norm['title']}"
        if session_key in seen:
            stats["deduped_in_session"] += 1
            continue
        seen.add(session_key)

        category = classify(norm["title"], norm["summary"])
        if norm.pop("_official_from_upstream", False):
            official = bool(norm.get("is_official"))
        elif norm.get("source_kind") == "official":
            # 账号名本身就是政务/官媒/机构/品牌号，无需再猜
            official = True
        else:
            official = is_official(norm["title"], norm["summary"], norm["source"])

        norm["category"] = category
        norm["is_official"] = official
        norm["collected_at"] = datetime.now().isoformat(timespec="seconds")
        norm["_seen_on"] = today_str
        cleaned.append(norm)
        stats["by_category"][category] = stats["by_category"].get(category, 0) + 1
        kind = norm.get("source_kind", "unknown")
        stats["by_source_kind"][kind] = stats["by_source_kind"].get(kind, 0) + 1

    stats["kept"] = len(cleaned)
    stats["today"] = today_str
    return {"articles": cleaned, "stats": stats}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="原始 JSON 输入路径")
    ap.add_argument("--output", required=True, help="处理后 JSON 输出路径")
    ap.add_argument("--history-file", default="data/history.json",
                    help="跨日去重历史库路径（默认 data/history.json）")
    ap.add_argument("--retention-days", type=int, default=30,
                    help="历史保留天数（默认 30）")
    ap.add_argument("--no-history", action="store_true",
                    help="跳过历史去重（强制当天内容重新推送）")
    ap.add_argument("--reset-history", action="store_true",
                    help="清空历史库后从头开始（慎用）")
    ap.add_argument("--no-entity-dedupe", action="store_true",
                    help="关闭活动级去重（同一活动换标题也会被当成新内容）")
    ap.add_argument("--skip-delivered-today", action="store_true",
                    help="同一天内只呈现本轮新增内容（默认是同日全量呈现）")
    ap.add_argument("--keep-expired", action="store_true",
                    help="保留已过期内容（默认丢弃结束日期早于今天的条目）")
    ap.add_argument("--keep-last-day", action="store_true",
                    help="保留「今天就是最后一天」的内容（默认丢弃，用户 2026-11-05 口径）")
    ap.add_argument("--keep-aggregator", action="store_true",
                    help="保留福利整理号（羊毛号/线报号/本地宝）的内容，默认剔除")
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    today_str = date.today().isoformat()
    history_path = Path(args.history_file)

    # 加载 / 重置历史库
    if args.reset_history:
        history = {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "retention_days": args.retention_days,
            "last_updated": None,
            "items": {},
        }
        save_history(history_path, history)
        print(f"[history] 已重置历史库：{history_path}")
    else:
        history = load_history(history_path)

    fingerprints = (set() if args.no_history
                    else build_history_fingerprints(history, today_str,
                                                    args.skip_delivered_today))
    if args.no_history:
        print(f"[history] 已跳过历史去重（--no-history），本次不会跟历史交叉过滤")
    else:
        seen_today = sum(
            1 for meta in history["items"].values()
            if meta.get("last_seen") == today_str
        )
        if seen_today and args.skip_delivered_today:
            print(f"[history] {seen_today} 条今天已交付过，按 --skip-delivered-today 不再展示")
        elif seen_today:
            print(f"[history] {seen_today} 条今天已整理过，按同日全量策略继续呈现")

    raw = json.loads(in_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("输入 JSON 必须是文章数组")

    if args.keep_expired:
        print("[process_articles] ⚠️ --keep-expired：已关闭过期过滤，过期内容也会展示")
    if args.keep_last_day:
        print("[process_articles] ⚠️ --keep-last-day：最后一天的内容也会展示")
    if args.keep_aggregator:
        print("[process_articles] ⚠️ --keep-aggregator：福利整理号的内容也会展示")

    activities = (
        [] if (args.no_history or args.no_entity_dedupe)
        else build_history_activities(history, today_str)
    )
    result = process(raw, fingerprints, today_str, activities,
                     keep_expired=args.keep_expired,
                     keep_last_day=args.keep_last_day,
                     keep_aggregator=args.keep_aggregator)

    for hit in result["stats"].get("aggregator_hits", []):
        print(f"[整理号剔除] [{hit['account']}] {hit['title'][:38]}")
    for hit in result["stats"].get("shared_url_titles", []):
        print(f"[拼盘拆条剔除] {hit['title'][:40]} ← {hit['url'][:56]}")
    for t in result["stats"].get("expired_titles", []):
        print(f"[过期剔除] {t[:46]}")
    for t in result["stats"].get("last_day_titles", []):
        print(f"[最后一天剔除] {t[:46]}")
    for hit in result["stats"].get("same_activity_hits", []):
        print(f"[活动级去重] 跳过「{hit['title'][:38]}」"
              f" → 与 {hit['first_seen']} 已推「{hit['matched'][:38]}」同一活动")

    # 写回历史库
    if not args.no_history:
        trim_history(history, date.today(), args.retention_days)
        new_count = update_history(history, result["articles"], today_str)
        save_history(history_path, history)
        result["stats"]["history_added"] = new_count
        result["stats"]["history_size"] = len(history["items"])
        print(f"[history] 本次新增 {new_count} 条指纹入库并标记已交付；历史库现有 {len(history['items'])} 条")

    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    s = result["stats"]
    print(f"[process_articles] {today_str} · "
          f"输入 {s['total']} 篇，"
          f"拼盘拆条剔除 {s.get('dropped_shared_url', 0)} 篇，"
          f"整理号剔除 {s.get('dropped_aggregator', 0)} 篇，"
          f"过期剔除 {s.get('dropped_expired', 0)} 篇，"
          f"最后一天剔除 {s.get('dropped_last_day', 0)} 篇，"
          f"历史跳过 {s['deduped_from_history']} 篇，"
          f"同一活动跳过 {s.get('deduped_same_activity', 0)} 篇，"
          f"当日去重 {s['deduped_in_session']} 篇，"
          f"保留 {s['kept']} 篇")
    by_cat = ", ".join(f"{k}={v}" for k, v in s["by_category"].items())
    print(f"[process_articles] 分类分布：{by_cat}")
    kinds = s.get("by_source_kind", {})
    print(f"[process_articles] 来源分布：官方号 {kinds.get('official', 0)} 篇，"
          f"整理号转述单一活动 {kinds.get('aggregator', 0)} 篇，"
          f"未识别 {kinds.get('unknown', 0)} 篇")


if __name__ == "__main__":
    main()
