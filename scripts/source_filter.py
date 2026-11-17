"""
来源账号过滤：区分「官方账号」与「福利整理账号」。

背景（用户 2026-11-05 硬要求）：
    只要官方账号的内容，不要福利整理账号的内容。

    source 字段的既有格式是「账号 · 发布主体」，例如：
        红包线报 · 安徽省法学会      ← 账号是线报号，主体才是官方
        成都本地宝 · 四川省商务厅    ← 账号是本地聚合平台，主体是官方
        遂宁新闻网·中共遂宁市委宣传部 ← 账号本身是官方媒体，放行
        瑞幸咖啡官方 · 深圳本地宝    ← 账号是品牌官方号，放行

    判定只看 `·` 前面的「账号」，不看后面的主体——因为用户要的是
    「官方自己发的」，不是「福利号转述官方活动」。

判定顺序（白名单优先，避免「XX市福利院」被「福利」误杀）：
    1. 剥平台壳：「微信公众号（全民淘福利）」→「全民淘福利」
    2. 官方白名单命中 → official（放行）
    3. 整理号黑名单命中 → aggregator（剔除）
    4. 其余 → unknown（保留，仅在数据里标注，便于后续人工复核）
"""
import re

# 平台外壳：采集时经常把渠道名和账号名糊在一起，如「微信公众号（上海本地宝」
_WRAPPER_RE = re.compile(
    r"^(?:微信公众号|微信订阅号|微信服务号|微信|公众号|"
    r"今日头条|头条号|头条|搜狐号|搜狐|网易号|网易|百家号|新浪看点|"
    r"微博|小红书|抖音|快手|B站|哔哩哔哩)\s*[（(]\s*(.+)$"
)

# 官方账号白名单：政府机关 / 官媒 / 金融机构 / 事业单位 / 品牌官方
# 只写在账号名里的**结构特征**，不做全量枚举（枚举必漏）
_OFFICIAL_ACCOUNT_RE = re.compile(
    r"(?:"
    # —— 党政机关 ——
    r"人民政府|政府$|市委$|区委$|县委$|州委$|镇委$|街道办|共青团|总工会|工会$|妇联|残联|红十字会|"
    r"管委会|管理局|监管局|市场监管|监管$|委员会|办公厅|办公室|"
    r"(?:教育|财政|商务|卫生健康|卫健|市场监管|应急管理|人力资源|社会保障|医疗保障|"
    r"文化和旅游|文广旅|生态环境|住房和城乡建设|交通运输|农业农村|水利|发展改革|"
    r"统计|司法|税务|公安|民政|体育|气象|自然资源|城市管理|综合执法|"
    r"文化和旅游|文广旅|旅游|人社|医保|社保|教科|"
    r"疾病预防控制|疾控|药品监督|烟草专卖)(?:局|厅|部|委|中心|办公室)|"
    r"医保$|社保$|科协$|政法委$|丝路法雨|^长安|科普网$|人社$|^中国普法|"
    r"疾控$|卫健$|文旅$|外事$|外办$|普法$|^法治|"
    r"减灾$|反走私$|清算中心$|打私办$|生态环境$|禁毒$|^网信|"
    r"应急管理$|"  # 应急管理局官方号简称（茂名应急管理等）
    r"交警$|"  # 公安交管官方号（郑州交警等）
    r"(?:消防|消防救援|消防救援支队|消防救援局)$|"
    r"(?:法院|检察院)$|"
    r"发布$|科普$|健康$|商务$|"
    # —— 官方媒体 ——
    r"(?:日报|晚报|报业|报业网|新闻网|新闻|电视台|广播|融媒|传媒|观察)$|"
    r"^川观|^澎湃|^新华社|^人民日报|^中工网|^新湖南$|^齐鲁壹点|^齐鲁晚报|^广西云$|"
    # —— 金融机构 ——
    r"(?:银行|农商行|农商银行|信用社|基金|证券|保险|信托|理财)$|"
    r"(?:分行|支行)$|信用卡买单吧$|"
    # —— 事业单位 / 院校 / 场馆 ——
    r"(?:大学|学院|图书馆|博物馆|美术馆|文化馆|科技馆|档案馆|医院|研究院|研究所|"
    r"协会|学会|联合会|基金会|杂志社|杂志|出版社)$|"
    # —— 品牌官方 ——
    r"官方$|旗舰店$|旗舰$|"
    r"^[^\s]{2,12}(?:餐饮|食品|乳业|药业|电器|汽车|航空|酒店|超市|便利店)$"
    r")"
)

# 整理号黑名单：高置信词，宁可漏判也不误杀官方号
_AGGREGATOR_TOKENS = [
    "羊毛", "线报", "福利", "优惠", "省钱", "薅", "撸羊毛", "撸", "返利",
    "凑单", "折扣", "特价", "白菜", "捡漏", "情报", "值得买", "本地宝",
    "周末去哪", "吃喝玩乐", "同城", "红包", "秒杀", "试用", "免费领",
    "省钱君", "福利社", "福利君", "福利汇", "优惠券", "券妈妈", "剁手",
]

# 黑名单例外：含黑名单词但本身是正规机构
_AGGREGATOR_EXCEPTIONS = re.compile(
    r"(福利院|福利中心|社会福利|儿童福利|福利彩票|"
    r"优惠利率|普惠|广播电视台)"
)

# 聚合式账号名：以「各…」开头，或把多个不同主体用斜杠并列
_COLLECTIVE_RE = re.compile(r"^(?:各|多家|各大|各类|全网|全国各)")

_SEP_RE = re.compile(r"[·・｜|]")


def _strip_wrapper(account: str) -> str:
    """剥掉「微信公众号（xxx」这类平台壳。"""
    a = (account or "").strip()
    m = _WRAPPER_RE.match(a)
    if m:
        a = m.group(1).strip()
    return a.strip(" （）()　[]【】")


def split_source(source: str) -> tuple:
    """把 source 拆成 (账号, 主体)。无分隔符时主体为空。"""
    s = (source or "").strip()
    if not s:
        return "", ""
    parts = _SEP_RE.split(s, maxsplit=1)
    if len(parts) == 2:
        return _strip_wrapper(parts[0]), parts[1].strip()
    return _strip_wrapper(s), ""


def classify_source(source: str) -> tuple:
    """返回 (kind, account, reason)。

    kind ∈ {"official", "aggregator", "unknown"}
    reason 是命中的规则说明，便于在 stats 里回溯为什么被剔除。
    """
    account, _subject = split_source(source)
    if not account:
        return "unknown", "", "来源为空"

    if _COLLECTIVE_RE.match(account):
        return "aggregator", account, f"聚合式账号名（{account}）"

    if _OFFICIAL_ACCOUNT_RE.search(account):
        return "official", account, ""

    if _AGGREGATOR_EXCEPTIONS.search(account):
        return "official", account, ""

    for tok in _AGGREGATOR_TOKENS:
        if tok in account:
            return "aggregator", account, f"账号名含「{tok}」"

    return "unknown", account, ""


def is_aggregator(source: str) -> bool:
    return classify_source(source)[0] == "aggregator"


# ---------------- 汇总拼盘判定 ----------------
#
# 用户 2026-11-05 二次确认口径（三选一里选了第二档）：
#   「只砍汇总拼盘：账号是整理号、且内容是『多活动汇总/合集/今日到期N连』
#     这种二手拼盘才砍；线报号转述的**单一**官方活动保留，
#     因为活动主体、规则、链接都是明确的。」
#
# 所以账号性质只是必要条件，还必须叠加「拼盘内容」才剔除。
# 反例（必须保留）：红包线报 · 南方基金「债基大闯关」答题抽现金
# 正例（必须剔除）：银行优惠活动 · 各银行APP「11月5日银行羊毛汇总」
#                 混喜线报 · 各品牌小程序「今日到期饮品免单四连」

# 拼盘式标题特征
_COLLECTIVE_TITLE_RE = re.compile(
    r"(汇总|合集|大盘点|盘点|一览|清单|速览|"
    r"今日到期|本(?:周|月)到期|本(?:周|月)截止|"
    r"[一二三四五六七八九十\d]+连(?!续)|"
    r"[一二三四五六七八九十\d]+(?:个|款|条|场|波)(?:活动|福利|红包|羊毛|优惠|好礼))"
)

# 拼盘式主体：「各银行APP」「各品牌小程序」「多家平台」这类不是单一主体
_COLLECTIVE_SUBJECT_RE = re.compile(r"^(?:各|多家|各大|各类|全网|全国各)")


def _is_collective_subject(source: str) -> bool:
    """主体（`·` 后面那半）是否本身就是「一堆主体的集合」。"""
    _account, subject = split_source(source)
    return bool(subject and _COLLECTIVE_SUBJECT_RE.match(subject.strip()))


def should_drop(title: str, summary: str, source: str) -> tuple:
    """是否应剔除。返回 (drop, reason, account)。

    只有「整理号 + 汇总拼盘」两个条件同时成立才剔除；
    整理号发的单一官方活动照常保留（用户 2026-11-05 口径）。
    """
    kind, account, why = classify_source(source)
    if kind != "aggregator":
        return False, "", account

    if _is_collective_subject(source):
        _acct, subject = split_source(source)
        return True, f"{why} + 主体是集合（{subject}）", account

    m = _COLLECTIVE_TITLE_RE.search(title or "")
    if m:
        return True, f"{why} + 标题含「{m.group(1)}」", account

    # 标题用顿号并列 ≥3 个活动（多活动拼盘）
    if (title or "").count("、") >= 3:
        return True, f"{why} + 标题并列多个活动", account

    return False, "", account


if __name__ == "__main__":
    # 自测：改判定规则后先跑这个
    CASES = [
        # (标题, source, 期望是否剔除, 说明)
        ("遂宁市交通安全知识竞赛", "遂宁新闻·遂宁市道路交通安全指挥部", False, "官媒+政务"),
        ("江苏工会有奖知识问答", "江苏工会·常州本地宝", False, "主办方自己发的"),
        ("益禾堂14周年抽奖", "益禾堂官方·合肥本地宝汇总", False, "品牌官方"),
        ("巴中市人民政府惠民券", "巴中市人民政府", False, "政务号"),
        ("成都「秋冬有礼」大礼包", "成都市文化广电旅游局 · 成都市商务局", False, "文旅局"),
        ("社会福利院开放日", "成都市社会福利院", False, "含「福利」但是正规机构"),
        ("11月5日银行羊毛汇总", "银行优惠活动 · 各银行APP", True, "整理号+主体集合"),
        ("今日到期饮品免单四连", "混喜线报 · 各品牌小程序", True, "整理号+主体集合"),
        ("11月3日银行羊毛汇总", "今日羊毛速报", True, "整理号+标题汇总"),
        ("本周福利合集", "成都本地宝·四川省商务厅", True, "整理号+合集"),
        ("南方基金「债基大闯关」答题抽现金", "红包线报 · 南方基金", False, "整理号发的单一活动"),
        ("吕梁市疾控食品安全答题", "红包线报 · 吕梁市疾控中心", False, "整理号发的单一活动"),
        ("某活动", "微信公众号（全民淘福利）", False, "整理号发的单一活动，保留"),
        ("今日福利汇总", "微信公众号（全民淘福利）", True, "剥平台壳后命中整理号+汇总"),
        ("浦发银行企微体验有礼", "浦发银行郑州分行 · 浦发银行", False, "银行分行官方号"),
        ("交行岁末狂欢享豪礼", "交通银行信用卡买单吧 · 交通银行信用卡中心", False, "银行信用卡官方号"),
        ("法治护航美丽湖南学法答题", "湖南生态环境 · 湖南省生态环境厅", False, "省厅政务号"),
        ("茂名冬季防火线上答题抽红包", "茂名应急管理 · 茂名市应急管理局", False, "应急管理局官方号简称"),
    ]
    bad = 0
    for title, source, want_drop, note in CASES:
        got, why, acct = should_drop(title, "", source)
        kind = classify_source(source)[0]
        ok = got == want_drop
        bad += 0 if ok else 1
        flag = "✅" if ok else "❌"
        print(f"{flag} drop={got!s:5s} kind={kind:10s} [{acct}] {note}"
              + (f"  ({why})" if why else ""))
    print(f"\n{len(CASES) - bad}/{len(CASES)} 通过")
    raise SystemExit(1 if bad else 0)

