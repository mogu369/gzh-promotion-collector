"""
gzh-promotion-collector / scripts / region_filter.py

地域筛选器：
  - 适合遂宁周边人员参与的活动（遂宁/四川/重庆/绵阳等临近地市）
  - 不限参与人员的活动（全国线上 / 不限地域）

用法：
  python scripts/region_filter.py \
      --input data/articles.json \
      --output-dir output/2026-09-04
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent))
from category_defs import CATEGORY_LABELS, CAT_ORDER

# ============ 用户所在城市配置（2026-11-17：取消遂宁硬编码，改为用户输入） ============
# 配置文件 data/config.json 由使用者填写自己的城市，示例：
#   {"home_city": "北京", "home_province": "北京", "nearby_cities": ["天津", "廊坊"]}
# 未配置时沿用旧的遂宁默认，保证老用户/历史数据行为不变。
CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"
_DEFAULT_CITY = "遂宁"          # 向后兼容默认值
_DEFAULT_PROVINCE = "四川"
_LOCAL_BUCKET_KEY = "suining"   # 内部桶名保持不变，避免下游脚本大改


def load_city_config(path: Path = None):
    """读取用户城市配置，返回 (city, province, nearby_cities)。"""
    p = path or CONFIG_PATH
    city, province, nearby = _DEFAULT_CITY, _DEFAULT_PROVINCE, []
    if p.exists():
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
            c = str(cfg.get("home_city") or "").strip()
            pv = str(cfg.get("home_province") or "").strip()
            if c:
                city = c
            if pv:
                province = pv
            nearby = [str(x).strip() for x in (cfg.get("nearby_cities") or []) if str(x).strip()]
        except Exception as e:
            print(f"[config] 读取 {p} 失败，沿用默认城市 {_DEFAULT_CITY}: {e}", file=sys.stderr)
    return city, province, nearby


def local_label(city: str) -> str:
    """本地桶的展示名，如「北京及周边」。"""
    return f"{city}及周边"


def build_local_keywords(city: str, province: str, nearby: list) -> set:
    """按用户城市动态生成本地白名单关键词。"""
    kws = {city, province}
    # 市辖区常见后缀：让「定位成都」「定位成都市」都能命中
    if city:
        kws.add(city + "市")
    if province:
        kws.add(province + "省")
    kws.update(nearby)
    return {k for k in kws if k}


# ============ 地域判定规则 ============

# 适合遂宁周边：遂宁本市 + 成渝经济圈临近地市（高铁2小时/车程3小时内可达）
SUINING_REGION_KEYWORDS = {
    "遂宁", "安居", "船山", "射洪", "蓬溪", "大英",
    "成都", "锦江", "高新", "武侯", "金牛", "成华",
    "绵阳", "涪城", "游仙", "安州",
    "德阳", "旌阳",
    "南充", "顺庆", "高坪", "嘉陵",
    "广安", "前锋", "广安",
    "达州", "通川",
    "资阳", "雁江",
    "内江", "市中", "东兴",
    "重庆", "渝中", "江北", "九龙坡", "沙坪坝", "南岸",
    "川工", "川观新闻", "四川工会", "四川", "蜀里安逸", "安逸驿站",
    "成渝", "川渝",
}

# 不限参与人员：全国 / 线上 / 无地域限制
UNRESTRICTED_KEYWORDS = {
    "全国", "不限", "线上", "APP", "小程序", "微信", "支付宝",
    "云闪付", "游戏", "论坛", "认证会员", "教师认证",
    "穿越火线", "CF", "腾讯频道", "瑞幸", "luckin", "星巴克",
    "蜜雪冰城", "奈雪", "益禾堂", "沪上阿姨", "书亦烧仙草",
    "网易云", "夸克", "WPS", "百度网盘", "扫描全能王",
    "微信小店", "小店", "金秋大促",
    "抖音", "APP", "满减券",
}

# 明确排除（仅限远地/特定身份，不适合遂宁周边）
EXCLUDE_REGION_KEYWORDS = {
    "上海黄浦", "上海之夏",
    "江苏工会", "江苏省总",
    "安徽工会", "安徽省总", "皖韵",
    "黄陂", "武汉黄陂",
    "福州", "遇见福州",
    "邯郸", "邯郸市委",
    "佛山",
    "祥符",
}


REGION_LABELS = {
    "suining": "本地及周边",   # main 里按用户城市覆盖为「XX及周边」
    "unrestricted": "不限地域",
    "exclude": "其他远地",
    "unknown": "未分类",
}

# 运行时本地关键词（由 main 按用户城市初始化；未初始化时用旧默认）
_LOCAL_REGION_KEYWORDS = SUINING_REGION_KEYWORDS | build_local_keywords(_DEFAULT_CITY, _DEFAULT_PROVINCE, [])


def init_local_region(city: str, province: str, nearby: list):
    """按用户城市重建本地桶关键词，并设置展示标签。"""
    global _LOCAL_REGION_KEYWORDS
    local = build_local_keywords(city, province, nearby)
    configured = (city != _DEFAULT_CITY) or bool(nearby)
    base = set(SUINING_REGION_KEYWORDS) if not configured else set()
    _LOCAL_REGION_KEYWORDS = base | local
    REGION_LABELS[_LOCAL_BUCKET_KEY] = local_label(city)


# ============ 参与可行性硬校验（2026-10-15 用户要求） ============
# 用户原话：「以前的区域限制等条件都要注意，不要推荐无法参与的活动」；
# 补充：「定位成都和重庆不要拦」——公众号受众含成都/重庆读者，
# 定位成都/重庆可参与的活动照常推荐。
# 「川内」≠「可参与」——需要定位到外地城市、限外地身份的活动，遂宁用户
# 实际参与不了，一律强制 exclude，即使人工标注了 _region_bucket 也要否决。
#
# 可参与 = 遂宁本地（含船山/安居/射洪/蓬溪/大英）
#        ｜省级活动全省可参与（定位四川即可，如蜀里安逸票根惠民）
#        ｜成都/重庆可参与的活动（受众覆盖，定位成都/重庆不拦）
#        ｜全国线上不限定位/身份
# 不可参与 = 需手机定位在其他外地城市（如定位泸州/德阳/天津）
#          ｜限其他外地身份（XX市职工/市民/定位用户/会员）
#
# ✦ 判定以「领取环节」为准（2026-10-15 用户口径：「书展要」）
#   线上可领 → 算可参与，即使**核销/使用**需线下到场。
#   例：天府书展 30 元无门槛券线上抢，仅在主展场线下用 → **收录**。
#   仅在领取环节就要求外地定位/外地现场才拦。
#   但必须在 summary 写明使用限制（如「限成都主展场线下使用」），让读者自行判断。
#
# ✦ 福建按外地处理（2026-10-15 用户确认「福建不要」），不进白名单。

# 地域白名单：这些地区开头的不算「外地」
# 2026-11-17 改为按用户城市动态生成；未配置时沿用旧的四川/遂宁/成渝默认。
_GENERIC_ALLOW_PREFIXES = ("全省", "全国", "全域", "川内", "川渝", "成渝", "全疆", "全区")
_LEGACY_ALLOW_PREFIXES = (
    # 遂宁及区县（旧默认）
    "遂宁", "船山", "安居", "射洪", "蓬溪", "大英",
    # 成都及区县
    "成都", "锦江", "青羊", "金牛", "武侯", "成华", "高新", "龙泉驿",
    "青白江", "新都", "温江", "双流", "郫都", "天府新区", "东部新区",
    "简阳", "都江堰", "彭州", "崇州", "邛崃", "大邑", "蒲江", "新津", "金堂",
    # 重庆及区县
    "重庆", "渝中", "渝北", "江北", "南岸", "九龙坡", "沙坪坝", "大渡口",
    "北碚", "巴南", "两江", "万州", "涪陵", "永川", "江津", "合川", "潼南",
    "铜梁", "大足", "荣昌", "璧山", "长寿", "綦江", "南川", "黔江", "垫江",
    "梁平", "丰都", "忠县", "开州", "云阳", "奉节", "巫山", "巫溪", "石柱",
    "秀山", "酉阳", "彭水", "城口", "武隆",
)
# 运行时白名单（由 main / self-test 初始化）
_LOCATION_ALLOW_PREFIXES = _GENERIC_ALLOW_PREFIXES + _LEGACY_ALLOW_PREFIXES


def init_location_allow(city: str, province: str, nearby: list):
    """按用户城市重建定位白名单：用户城市/省份/周边城市一律视为「本地不算外地」。

    旧的遂宁/成渝默认仅在用户未配置（city==默认遂宁）时保留；
    一旦用户配置了别的城市，旧白名单移除，避免把「定位遂宁」当成可参与。
    """
    global _LOCATION_ALLOW_PREFIXES
    local = build_local_keywords(city, province, nearby)
    configured = (city != _DEFAULT_CITY) or (province != _DEFAULT_PROVINCE) or bool(nearby)
    legacy = _LEGACY_ALLOW_PREFIXES if not configured else ()
    _LOCATION_ALLOW_PREFIXES = tuple(_GENERIC_ALLOW_PREFIXES) + tuple(sorted(local)) + tuple(legacy)

# ① 需定位到外地城市：「定位泸州」「定位在德阳市」「定位地址在天津市」
#    ⚠️ 2026-10-22 修复：「需手机定位与扫码城市一致方可领券」被读成「与扫码城市」→
#    「扫码城市」不在白名单 → 整篇被否决。改为排除「与/和/跟/同即」引导的**条件式定位**
#    （即定位要求的是「与某参数一致」，而非「定位在某地」），这类是软条件，交给人工判断。
#    后缀补 `\s|$`：原写法必须紧跟 市/区/县/的，导致「需定位泸州才能领」整句漏拦。
#    ⚠️ 2026-10-28 修复两处误杀：
#    (a)「将手机**定位设置为**遂宁市」只吃掉「为」，捕获成「设置为遂宁」→ 不在白名单 → 射洪砂锅券被否决。
#        动词表补全「设置为/设为/设定为/改成/改为/调成/调到」，且长的放前面（正则交替按顺序匹配）。
#    (b)「需定位**特定城市**」「定位**外地城市**」是泛指描述，不是具体地名，却被当成 2-6 字地名捕获。
#        新增 _VAGUE_PLACES 黑名单，命中即跳过这一次匹配（继续找下一个），而不是直接判 exclude。
_RE_LOC_VERB = r"(?:设置为|设定为|设为|改成|改为|调成|调到|在|于|为|到)?"
_RE_BLOCK_LOCATION = re.compile(
    r"(?<![不无没免])定位(?:地址)?" + r"\s*" + _RE_LOC_VERB + r"\s*"
    r"(?![与和跟同即])"
    r"([\u4e00-\u9fa5]{2,6}?)(?:市|区|县|州|盟|地区|的|\s|$)"
)

# 泛指地名：不是具体行政区，不能据此判定「需定位外地」。
# 例：「部分活动带地域标记需定位特定城市」「需定位到外地城市」——都是说明性文字。
_VAGUE_PLACES = (
    "特定", "外地", "外省", "其他", "其它", "当地", "本地", "所在", "指定",
    "限定", "相应", "相关", "目标", "活动",
)
# 人群限定词：「限新用户」「限女性用户」不是地域限制。
# ⚠️ 用**精确匹配**而非前缀匹配——「新」若当前缀会误伤「新疆」「新都」。
_NON_PLACE_WORDS = {
    "新", "老", "新客", "老客", "本", "该", "此", "各",
    "女性", "男性", "学生", "教师", "儿童", "成人", "老人", "青少年",
    "会员", "个人", "企业", "商家", "普通", "特殊", "优质", "活跃",
    "受邀", "特邀", "内部", "员工", "粉丝", "注册", "实名", "认证",
    # 2026-11-06 修复：「仅限已关注用户」被当成身份门槛，中欧基金点赞红包被误杀。
    # 关注公众号人人可做、不花钱，不是外地身份限制。
    "已关注", "关注",
}
# ② 限外地身份：「限广西区内职工」「限武汉市民」「限江苏工会会员」
#    （注意：「用户」一词不单独作后缀，避免误拦「限新用户/限女性用户」这类人群限定）
#    ⚠️ 2026-10-21 修复：`限` 前加否定 lookbehind。
#    原实现把「**不**限用户户籍与手机定位」读成「限用户户籍」，
#    导致重庆有奖发票「开心季」（用户明确要收的重庆渠道）被硬校验误杀成 exclude。
#    ⚠️ 2026-10-28 补后缀：原表只认 职工/市民/居民/会员/户籍，漏了「限江苏地区」「限广东省内」
#    以及「仅限江苏用户」这类写法。补 地区/省内/全省/省/市/用户；
#    「用户」带来的误伤（限新用户/限女性用户）由 _NON_PLACE_WORDS 精确词表兜住。
_RE_BLOCK_IDENTITY = re.compile(
    r"(?<![不无没免])限([\u4e00-\u9fa5]{2,6}?)(?:职工|市民|居民|定位用户|会员|户籍|参保|工会会员|地区|省内|全省|省|市|用户)"
)

# ③ 裸「限＋省名」：标题里常缩写为「30元无门槛券（限江苏）」。
#    上面那条靠后缀识别，对裸写会漏。这里用明确省名清单兜底——
#    **不用 2-6 字通配**，否则「限时/限价/限额」这类会被误伤。
_FAR_REGION_NAMES = (
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "贵州", "云南", "西藏",
    "陕西", "甘肃", "青海", "宁夏", "新疆", "香港", "澳门", "台湾",
)
_RE_BLOCK_REGION_BARE = re.compile(
    r"(?<![不无没免])限(" + "|".join(_FAR_REGION_NAMES) + r")"
)

# ④ 「参与对象/面向对象 为 X省职工」：不带「限」字的隐性地域门槛
#    （2026-11-06 案例：广西互助保障答题「参与对象为广西职工」曾被当不限地域放行）。
#    只认「参与对象为/面向…为」引导的表述，避免误伤普通叙述；
#    「全体/全民/所有/广大」等泛指由下面的 vague 判断兜住。
_RE_BLOCK_AUDIENCE = re.compile(
    r"(?:参与对象|面向对象|服务对象|参与人群|活动对象)[为是]([\u4e00-\u9fa5]{2,6}?)(?:职工|市民|居民|用户|参保人)"
)


# ============ 参与可用性硬校验（2026-11-17 用户反馈后新增） ============
# 用户原话：「昨天的 8 篇只有 3、4、6 能用，其余完全不能用」。
# 复盘：11/09、11/11、11/12 三批放宽把「点进去也参与不了」的活动放了进来：
#   ① 要下载独立APP ② 参与前实名填姓名电话 ③ 要拉好友点赞/转发朋友圈
#   ④ 入口尚未开放 ⑤ 常设展览（无场次无预约，随时可去不算福利）
# 这五类不靠地域/身份判定，是「可用性」问题，命中即 exclude。
# ⚠️ 边界：中奖后填姓名手机号属正常领奖（11/11 口径），不在②的拦截范围；
#    纯海报活动照收（11/11 口径），不在任何一条的拦截范围。

_RE_NEED_APP_DOWNLOAD = re.compile(
    r"应用(?:市场|商店)[^。；]{0,12}下载|下载[^。；]{0,12}(?:APP|客户端|应用)"
    r"|(?:安装|打开)[^。；]{0,8}(?:APP|客户端)(?:才能|方可|后参与)"
)
# 否定词：摘要常写「无需下载APP / 不用装客户端 / 不必下载应用」来自证零门槛，
# 不能把这类否定句当成「要下载APP」误伤（2026-11-17 茂名应急案例，同类于
# 常设展览「不用领票」被「领票」误伤）。匹配点前 6 字内出现否定词即跳过。
_RE_APP_NEGATION = re.compile(r"无需|无须|不用|不必|不需|勿需|不需要|不需要装")


def _find_positive(regex, text: str, negation=None):
    """找第一个「非否定语境」的命中，返回 match 或 None。"""
    for m in regex.finditer(text):
        prefix = text[max(0, m.start() - 6):m.start()]
        if negation and negation.search(prefix):
            continue
        return m
    return None
_RE_PRE_REALNAME = re.compile(
    r"答题[^。；]{0,12}实名制|实名(?:注册|登记)[^。；]{0,8}(?:方可|才能|后参与)"
    r"|如实(?:填写|填)[^。；]{0,6}姓名[^。；]{0,10}(?:联系电话|手机号|电话)"
)
_RE_SOCIAL_SHILL = re.compile(
    r"邀请好友[^。；]{0,12}点赞|转发[^。；]{0,6}朋友圈|分享[^。；]{0,10}朋友圈[^。；]{0,12}(?:参与|才能|即可|方可)"
    r"|集赞|集满[^。；]{0,6}赞"
)
_RE_ENTRY_NOT_OPEN = re.compile(
    r"(?:预约|报名|参与|领取)?入口[^。；]{0,12}(?:尚未|还未|暂未|未)开放"
    r"|敬请期待|等[^。；]{0,8}公告[^。；]{0,8}(?:再|才|第一时间)"
)
# 「摄影展」「美术书法联展」都要命中，但「展览馆」不算（用负向前瞻排除）
_RE_STATIC_EXHIBIT = re.compile(r"(?:摄影|书画|美术|文物|艺术)展(?![览馆])")


def usability_block_reason(article: dict) -> str:
    """返回活动「即使点进去也参与不了 / 不构成限时福利」的原因；可用返回空串。

    与地域校验独立，命中优先级相同（都强制 exclude）。
    """
    title = article.get("title", "")
    text = (title + " " + article.get("summary", "")) \
        .replace("（", "(").replace("）", ")")
    t = title.replace("（", "(").replace("）", ")")

    m = _find_positive(_RE_NEED_APP_DOWNLOAD, text, _RE_APP_NEGATION)
    if m:
        return f"需下载独立APP/客户端才能参与（…{m.group(0)}…）"
    m = _RE_PRE_REALNAME.search(text)
    if m:
        return f"参与前须实名填写姓名/手机号（…{m.group(0)}…）"
    m = _RE_SOCIAL_SHILL.search(text)
    if m:
        return f"需转发朋友圈/邀请好友点赞才能参与（…{m.group(0)}…）"
    m = _RE_ENTRY_NOT_OPEN.search(text)
    if m:
        return f"参与入口尚未开放（…{m.group(0)}…）"
    # 常设展览：标题是「X展」+ 摘要写「直接到场/无需预约/不用领票」+ 非节庆场次
    if (_RE_STATIC_EXHIBIT.search(t)
            and re.search(r"直接到场|无需预约|不用领票|常年|长期开放", text)
            # 注意否定词表里不能放「领票」——常设展览常写「不用领票」，会被自己否掉
            and not re.search(r"艺术节|文化节|演出季|场次|放票|预约入口", text)):
        return "常设展览，无场次无预约、随时可去，不构成限时福利"

    return ""


def _self_test_usability():
    """五条硬校验自测：5 该拦 + 4 不该拦。"""
    cases = [
        # (标题, 摘要, 是否应拦)
        ("广西云健康素养答题", "在应用市场下载「广西云」客户端，从首页答题入口进入", True),
        ("随州宪法宣传周答题", "线上答题实行实名制，需如实填姓名、联系电话和所在区域", True),
        ("海南消防每日一题", "在文末留言区写下答案，邀请好友为自己的留言点赞", True),
        ("东盟周电影展映", "预约入口今天尚未开放，先关注公众号等排片公告", True),
        ("王琦高原生态摄影展", "面向社会免费开放，无需预约、不用领票，直接到场即可", True),
        # 不该拦：中奖后填信息、纯海报、本地节庆、常规公众号菜单答题
        ("日照长护险答题", "关注公众号点菜单答题，中奖后在「我的奖品」查看领取", False),
        ("河北消防学消防知识赢红包", "点击文末「原文链接」进入答题专区，积分可以抽红包", False),
        ("遂宁涪江文化艺术节", "9项惠民展演免费看，11月19日黄桷树广场群众音乐会，免费展览11月18日起", False),
        ("长沙消防线上嘉年华", "关注公众号进入小程序报名，打卡攒积分兑换文创，包邮到家", False),
        # 否定句不得误伤：自证「无需下载APP/不用装客户端」的零门槛活动（2026-11-17 茂名应急）
        ("茂名应急冬季防火答题", "关注公众号即可参与，无需注册、无需下载APP，微信内H5答题得积分兑现金红包", False),
        ("某答题", "不用下载客户端，扫码进入小程序答题即可", False),
    ]
    ok = 0
    for title, summary, expect in cases:
        got = bool(usability_block_reason({"title": title, "summary": summary}))
        flag = "OK" if got == expect else "FAIL"
        if got == expect:
            ok += 1
        print(f"{flag} expect={expect!s:5} got={got!s:5} {title[:22]}")
    print(f"usability self-test: {ok}/{len(cases)} passed")
    return ok == len(cases)


def _is_allowed_place(place: str) -> bool:
    """地名是否落在可参与白名单内（遂宁/四川/成都/重庆/全国）。"""
    return place.startswith(_LOCATION_ALLOW_PREFIXES)


def _is_vague_place(place: str) -> bool:
    """非行政地名（特定城市/外地城市/新用户/女性用户…）不可据此否决。"""
    return place in _NON_PLACE_WORDS or place.startswith(_VAGUE_PLACES)


def participation_block_reason(article: dict) -> str:
    """返回该活动「无法参与」的原因；可参与则返回空串。

    只拦「领取环节」的硬条件：定位/身份限定外地。
    **不拦「核销/使用需线下到场」**——领券环节线上可参与的就算可参与
    （2026-10-15 用户口径「书展要」：天府书展线上抢券、主展场线下用，收录）。
    属地软条件由 agent 在标注 _region_bucket 时按 SKILL.md 参与可行性三问把关。

    ⚠️ 2026-10-28：改为遍历**全部**匹配，只有出现「具体地名且不在白名单」才否决。
    原写法只看第一个匹配就 return，导致「不限定位(白名单)…另有一段需定位外地」这类
    混合描述会被第一段放过、却被或漏判；现在任意一段硬命中就拦，泛指地名一律跳过。
    """
    # 2026-11-17：可用性硬校验优先于地域校验——「点进去也参与不了」先拦。
    u = usability_block_reason(article)
    if u:
        return u
    text = (article.get("title", "") + " " + article.get("summary", "")) \
        .replace("（", "(").replace("）", ")")
    for m in _RE_BLOCK_LOCATION.finditer(text):
        place = m.group(1)
        if _is_vague_place(place) or _is_allowed_place(place):
            continue
        return f"需定位到外地（…{m.group(0)}…）"
    for m in _RE_BLOCK_IDENTITY.finditer(text):
        place = m.group(1)
        if _is_vague_place(place) or _is_allowed_place(place):
            continue
        return f"限外地身份（…{m.group(0)}…）"
    m = _RE_BLOCK_REGION_BARE.search(text)
    if m:
        return f"限外地身份（…{m.group(0)}…）"
    for m in _RE_BLOCK_AUDIENCE.finditer(text):
        place = m.group(1)
        if (_is_vague_place(place) or _is_allowed_place(place)
                or place in {"全体", "全民", "所有", "广大", "符合"}):
            continue
        return f"限外地身份（…{m.group(0)}…）"
    return ""


def classify_region(article: dict) -> str:
    """返回 'suining' / 'unrestricted' / 'exclude' / 'unknown'

    优先使用显式标注的 _region_bucket（人工/agent 判定，最准），
    未标注时才退回关键词启发式。
    但**参与可行性硬校验优先于一切**：标注错了也会被否决并记录原因。
    """
    reason = participation_block_reason(article)
    override = article.get("_region_bucket")
    if reason:
        if override and override != "exclude":
            article["_region_vetoed"] = f"{override}→exclude：{reason}"
        return "exclude"
    if override in ("suining", "unrestricted", "exclude"):
        return override

    text = (article.get("title", "") + " " + article.get("summary", "")) \
        .replace("（", "(").replace("）", ")")

    # 1. 显式排除优先
    for kw in EXCLUDE_REGION_KEYWORDS:
        if kw in text:
            return "exclude"

    # 2. 适合用户本地及周边（按 config.json 城市动态判定）
    for kw in _LOCAL_REGION_KEYWORDS:
        if kw in text:
            return "suining"

    # 3. 不限参与人员（全国/线上/认证）
    for kw in UNRESTRICTED_KEYWORDS:
        if kw in text:
            return "unrestricted"

    # 4. 兜底：未命中，按不限处理（避免误杀）
    return "unrestricted"


def render_region_dashboard(articles, region_label, region_key, data_source, output_path,
                            skipped_from_history: int = 0, total_collected: int = 0):
    """渲染地域仪表盘 HTML（按 category 分组，顶部展示地域标签 + 统计）

    skipped_from_history / total_collected 可选传入；当上游 process_articles.py 进行了
    跨日去重时，会展示「今日共采集 N 篇，其中 M 篇已推荐过，未重复推送」提示。
    """
    by_cat = defaultdict(list)
    for a in articles:
        by_cat[a.get("category", "other")].append(a)

    cat_emoji = {k: v[0] for k, v in CATEGORY_LABELS.items()}
    cat_order = CAT_ORDER

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(articles)

    # 颜色配置
    if region_key == "suining":
        accent = "#d63031"
        banner_bg = "linear-gradient(135deg, #ff6b6b, #c0392b)"
    elif region_key == "unrestricted":
        accent = "#0984e3"
        banner_bg = "linear-gradient(135deg, #74b9ff, #0984e3)"
    else:
        accent = "#636e72"
        banner_bg = "linear-gradient(135deg, #b2bec3, #636e72)"

    html_parts = []
    html_parts.append(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{region_label} - 福利推文仪表盘</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
         margin: 0; background: #f5f6fa; color: #2c3e50; line-height: 1.6; }}
  header {{ background: {banner_bg};
           color: #fff; padding: 32px 24px; }}
  header h1 {{ margin: 0 0 8px; font-size: 24px; }}
  header .meta {{ opacity: 0.85; font-size: 14px; }}
  .stats {{ display: flex; gap: 16px; flex-wrap: wrap; padding: 20px 24px; }}
  .stat-card {{ background: #fff; border-radius: 8px; padding: 14px 18px;
               box-shadow: 0 2px 6px rgba(0,0,0,0.05); min-width: 140px; }}
  .stat-card .num {{ font-size: 26px; font-weight: bold; color: {accent}; }}
  .stat-card .lbl {{ color: #7f8c8d; font-size: 13px; margin-top: 4px; }}
  main {{ padding: 0 24px 40px; display: grid;
         grid-template-columns: repeat(auto-fit, minmax(360px, 1fr));
         gap: 20px; }}
  .category-section {{ background: #fff; border-radius: 12px;
                      box-shadow: 0 2px 8px rgba(0,0,0,0.06); overflow: hidden; }}
  .category-header {{ padding: 16px 20px; display: flex; align-items: center;
                     gap: 8px; font-weight: bold;
                     border-bottom: 2px solid {accent}; }}
  .category-header .count {{ background: {accent}; color: #fff;
                            padding: 2px 10px; border-radius: 12px; font-size: 13px; }}
  .article {{ padding: 14px 20px; border-top: 1px solid #ecf0f1;
             transition: background 0.15s; }}
  .article:hover {{ background: #f8f9fa; }}
  .article a {{ color: #2c3e50; text-decoration: none; font-weight: 500; }}
  .article a:hover {{ color: {accent}; }}
  .article .meta {{ font-size: 12px; color: #7f8c8d; margin-top: 4px;
                   display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
  .copy-btn {{ margin-left: auto; padding: 3px 10px; border: 1px solid #dcdde1;
              background: #f8f9fa; color: #2c3e50; border-radius: 12px;
              cursor: pointer; font-size: 11px; transition: 0.2s; }}
  .copy-btn:hover {{ background: {accent}; color: #fff; border-color: {accent}; }}
  .copy-btn.copied {{ background: #27ae60; color: #fff; border-color: #27ae60; }}
  .badge {{ display: inline-block; padding: 1px 8px; border-radius: 10px;
           font-size: 11px; font-weight: bold; }}
  .badge.official {{ background: #f1c40f; color: #2c3e50; }}
  .badge.region {{ background: {accent}; color: #fff; }}
  footer {{ padding: 20px 24px; text-align: center; color: #95a5a6; font-size: 12px; }}
  .empty {{ padding: 40px; text-align: center; color: #95a5a6; }}
</style>
</head>
<body>
<header>
  <h1>{region_label}</h1>
  <div class="meta">生成时间：{generated_at} · 数据源：{data_source} · 共 {total} 篇</div>
</header>
<div class="stats">
  <div class="stat-card"><div class="num">{total}</div><div class="lbl">总篇数</div></div>
  <div class="stat-card"><div class="num">{sum(1 for a in articles if a.get('is_official'))}</div><div class="lbl">官方/品牌</div></div>
""" + "".join(
        f'  <div class="stat-card"><div class="num">'
        f'{sum(1 for a in articles if a.get("category") == cat)}</div>'
        f'<div class="lbl">{lbl.split()[0]} {lbl.split()[-1]}类</div></div>\n'
        for cat, (lbl, _) in CATEGORY_LABELS.items() if cat != "other"
    ) + f"""</div>
<main>
""")

    if total == 0 and skipped_from_history > 0:
        html_parts.append(
            f'<div class="empty" style="background:#fffbe6;border-radius:12px;'
            f'padding:32px;font-size:15px;color:#5d4a00;">'
            f'📭 今日 {region_label} 桶无新内容<br>'
            f'<small style="color:#8a7a1a;">本次采集 {total_collected} 篇，全部 {skipped_from_history} 篇'
            f'已在今天之前的推荐轮次出现过，已被跨日去重过滤（同一天内的内容不会重复过滤）</small></div>'
        )
    elif total == 0:
        html_parts.append('<div class="empty">📭 当前无符合条件的活动</div>')
    else:
        for cat in cat_order:
            items = by_cat.get(cat, [])
            if not items:
                continue
            html_parts.append(f'<div class="category-section">')
            html_parts.append(
                f'<div class="category-header">'
                f'<span>{cat_emoji.get(cat, "📌 其他")}</span>'
                f'<span class="count">{len(items)}</span>'
                f'</div>'
            )
            for a in items:
                badges = []
                if a.get("is_official"):
                    badges.append('<span class="badge official">★ 官方</span>')
                region_tag = a.get("_region", "")
                if region_tag:
                    badges.append(f'<span class="badge region">📍 {region_tag}</span>')

                html_parts.append(f"""
<div class="article">
  <a href="{a.get('url', '#')}" target="_blank">{a.get('title', '')}</a>
  <div class="meta">
    <span>📅 {a.get('publish_date', '')}</span>
    <span>📰 {a.get('source', '')}</span>
    {' '.join(badges)}
    <button class="copy-btn" data-url="{a.get('url', '#')}">📋 复制链接</button>
  </div>
  <div style="margin-top: 6px; font-size: 13px; color: #555;">{a.get('summary', '')}</div>
</div>
""")
            html_parts.append('</div>')

    html_parts.append("""
</main>
<footer>
  由 gzh-promotion-collector Skill 自动生成 · 地域分类筛选模式
</footer>
<script>
  document.querySelectorAll('.copy-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      e.stopPropagation();
      const url = btn.dataset.url;
      if (!url || url === '#') { btn.textContent = '无链接'; return; }
      try {
        await navigator.clipboard.writeText(url);
        const original = btn.textContent;
        btn.textContent = '✓ 已复制';
        btn.classList.add('copied');
        setTimeout(() => { btn.textContent = original; btn.classList.remove('copied'); }, 1500);
      } catch (err) {
        const ta = document.createElement('textarea');
        ta.value = url;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        btn.textContent = '✓ 已复制';
      }
    });
  });
</script>
</body>
</html>
""")

    output_path.write_text("".join(html_parts), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="按地域分类生成两个仪表盘")
    parser.add_argument("--input", required=True, help="articles.json 路径")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    parser.add_argument("--data-source", default="联网搜索聚合", help="数据源标签")
    parser.add_argument("--city", default=None, help="覆盖 config.json 的本地城市（如 北京）")
    parser.add_argument("--province", default=None, help="覆盖 config.json 的省份（如 北京市）")
    parser.add_argument("--nearby", default=None, help="覆盖周边城市，逗号分隔（如 天津,廊坊）")
    args = parser.parse_args()

    # 读取用户城市配置：命令行参数 > config.json > 默认值
    city, province, nearby = load_city_config()
    if args.city:
        city = args.city.strip()
    if args.province:
        province = args.province.strip()
    if args.nearby:
        nearby = [x.strip() for x in args.nearby.split(",") if x.strip()]
    init_location_allow(city, province, nearby)
    init_local_region(city, province, nearby)
    print(f"[地域] 本地城市：{city}（省/直辖市：{province}）"
          + (f"，周边：{'/'.join(nearby)}" if nearby else ""))

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    data = json.loads(input_path.read_text(encoding="utf-8"))
    articles = data.get("articles", [])
    stats = data.get("stats", {}) or {}
    skipped_from_history = int(stats.get("deduped_from_history", 0))
    total_collected = int(stats.get("total", len(articles)))

    # 分类
    buckets = {"suining": [], "unrestricted": [], "exclude": [], "unknown": []}
    for a in articles:
        r = classify_region(a)
        a["_region_tag"] = r
        a["_region"] = REGION_LABELS.get(r, "")
        buckets[r].append(a)

    # 输出统计
    print(f"\n=== 地域分类结果 ===")
    print(f"  适合{local_label(city)}：{len(buckets['suining'])} 篇")
    print(f"  不限参与人员：{len(buckets['unrestricted'])} 篇")
    print(f"  无法参与（已排除）：{len(buckets['exclude'])} 篇")
    vetoed = [a for a in articles if a.get("_region_vetoed")]
    if vetoed:
        print(f"\n  ⚠️ 参与可行性否决 {len(vetoed)} 篇（人工标注被硬校验推翻）：")
        for a in vetoed:
            print(f"    - {a['title'][:44]}  [{a['_region_vetoed']}]")
    print()

    # 渲染两个仪表盘
    render_region_dashboard(
        buckets["suining"],
        f"📍 适合{local_label(city)}人员参与的活动",
        "suining",
        args.data_source,
        output_dir / "suining-region.html",
        skipped_from_history=skipped_from_history,
        total_collected=total_collected,
    )
    print(f"✓ 生成: {output_dir / 'suining-region.html'}")

    render_region_dashboard(
        buckets["unrestricted"],
        "🌐 不限参与人员的活动（全国/线上）",
        "unrestricted",
        args.data_source,
        output_dir / "unrestricted-region.html",
        skipped_from_history=skipped_from_history,
        total_collected=total_collected,
    )
    print(f"✓ 生成: {output_dir / 'unrestricted-region.html'}")

    # 输出一个合并 JSON，便于复用
    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_source": args.data_source,
        "home_city": city,
        "home_province": province,
        "nearby_cities": nearby,
        "local_label": local_label(city),
        "suining_region": [a for a in buckets["suining"]],
        "unrestricted": [a for a in buckets["unrestricted"]],
        "excluded_other_regions": [
            # 排除原因优先级：参与可行性硬否决 > agent 人工标注的 _exclude_reason > 兜底文案。
            # ⚠️ 2026-10-28：新增「内容类型不符」类排除（如双11红包口令、外地折扣券），
            # 这类没有地域/身份硬条件，若不支持 _exclude_reason 会全被兜底文案盖成
            # "仅限其他远地/特定身份"，用户看到的原因与实际不符。
            {
                "title": a["title"],
                "reason": (a.get("_region_vetoed")
                           or a.get("_exclude_reason")
                           or "仅限其他远地/特定身份"),
            }
            for a in buckets["exclude"]
        ],
        "stats": {
            "total": len(articles),
            "suining": len(buckets["suining"]),
            "unrestricted": len(buckets["unrestricted"]),
            "excluded": len(buckets["exclude"]),
        },
    }
    (output_dir / "region-classified.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"✓ 生成: {output_dir / 'region-classified.json'}")


def _self_test_city():
    """城市可配置自测：默认遂宁 + 自定义北京两组（每组先初始化再跑用例）。"""
    ok_all = True
    dc, dp, dn = load_city_config()
    groups = [
        ("默认遂宁", dc, dp, dn, [
            ("遂宁消费券", "遂宁市餐饮券满100减30", "suining"),
            ("成都电影券", "成都光影惠民观影", "suining"),
            ("定位泸州券", "需手机定位在泸州市才能领", "exclude"),
        ]),
        ("自定义北京", "北京", "北京", ["天津", "廊坊"], [
            ("北京消费券", "北京市餐饮券满100减30", "suining"),
            ("天津周边券", "天津滨海新区消费券", "suining"),
            ("定位遂宁券", "需手机定位在遂宁市才能领", "exclude"),
            ("定位上海券", "需定位上海市领取", "exclude"),
            ("全国答题", "全国用户均可参与答题抽红包", "unrestricted"),
        ]),
    ]
    for group, city, province, nearby, cases in groups:
        init_location_allow(city, province, nearby)
        init_local_region(city, province, nearby)
        ok = 0
        for t, s, exp in cases:
            got = classify_region({"title": t, "summary": s})
            good = got == exp
            ok += good
            print(f"{'OK' if good else 'FAIL'} [{group}] {t}: {got} (期望 {exp})")
        print(f"  {group}: {ok}/{len(cases)}")
        ok_all &= ok == len(cases)
    # 复位为配置文件的值，避免影响同进程后续调用
    c, p, n = load_city_config()
    init_location_allow(c, p, n); init_local_region(c, p, n)
    return ok_all


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(0 if _self_test_usability() else 1)
    if "--self-test-city" in sys.argv:
        sys.exit(0 if _self_test_city() else 1)
    main()