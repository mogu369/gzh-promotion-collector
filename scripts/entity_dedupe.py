"""
活动级去重（entity dedupe）——解决「同一活动换个标题/换个链接又被推一遍」。

背景（用户硬规则，2026-09-24）：
    推荐过的内容不要再推荐，**不管活动有没有结束**。
    仅靠 URL / 标题精确指纹不够：同一个活动会被不同媒体写成不同标题、
    挂在不同域名上（遂宁市商务局官网 vs 四川新闻网 vs 百度百科），
    于是「遂宁双节宠客九条」「四川省级有奖发票」这类活动反复出现在推荐里。

判定思路（保守优先，宁可漏判不可误杀）：
    1. 主地域（primary place）：标题里出现的最具体地域名；
    2. 活动类型（act tokens）：消费券 / 有奖发票 / 答题竞答 / 抽奖 / 红包 …；
    3. 主地域相同（或一方包含另一方）且活动类型有交集 → 视为同一活动；
    4. 兜底：标题归一化后相似度 >= 0.8 → 视为同一活动。

用法（供 process_articles.py 调用）：
    from entity_dedupe import is_same_activity, extract_acts, primary_place
    is_same_activity("遂宁双节宠客九条…1000万元综合消费券", "遂宁双节「宠客九条」：发放1000万元综合消费券")
"""
import difflib
import re

# 地名词（按需要自行扩充；越靠后/越长越具体）
PLACE_WORDS = [
    # 遂宁及周边
    "遂宁", "安居区", "船山区", "射洪", "蓬溪", "大英", "河东新区",
    # 成渝圈
    "成都", "天府新区", "大邑", "成华", "龙泉驿", "锦江", "金牛", "新都", "温江", "郫都",
    "绵阳", "德阳", "南充", "广安", "达州", "资阳", "内江", "眉山", "自贡", "泸州",
    "乐山", "宜宾", "广元", "雅安", "巴中", "攀枝花", "凉山", "甘孜", "阿坝",
    "重庆", "九龙坡", "渝中", "江北", "万州",
    # 省级 / 全国
    "四川", "四川省", "省级", "成德眉资", "成渝",
    # 其他常见省市（用于排除远地条目时也能识别）
    "云南", "福建", "广东", "深圳", "东莞", "广州", "佛山", "北京", "上海", "天津",
    "江苏", "浙江", "山东", "河南", "湖北", "湖南", "安徽", "江西", "河北", "山西",
    "陕西", "甘肃", "青海", "宁夏", "内蒙古", "广西", "海南", "贵州", "黑龙江", "吉林",
    "辽宁", "新疆", "西藏", "香港", "澳门", "台湾",
]

# 活动类型词（同义词已归并到同一 token）
ACT_SYNONYMS = [
    (r"有奖发票|发票抽奖|索票", "有奖发票"),
    (r"汽车消费券|购车补贴|汽车券|购车券", "汽车消费券"),
    (r"消费券|惠民券|满减券|优惠券", "消费券"),
    (r"知识竞答|有奖竞答|有奖问答|有奖答题|网上答题|网络答题|答题闯关", "答题竞答"),
    (r"抽奖|抽签|盲盒|抽取", "抽奖"),
    (r"红包|现金奖励|奖励金", "红包"),
    (r"免单|0元|0\.01元|免费领", "免单"),
    (r"立减金|刷卡金|支付立减", "立减金"),
    (r"会员|月卡|季卡|年卡", "会员权益"),
    (r"免费早餐|暖心餐", "免费早餐"),
    (r"景区免费|免费开放|免门票", "景区免费"),
    (r"砂锅券|美食券|餐饮券", "餐饮券"),
    (r"月饼|礼盒|粽子", "节日礼盒"),
    (r"演唱会门票|演出门票|观影券|电影券", "票务赠送"),
    (r"流量包|话费", "通信福利"),
    (r"补贴", "补贴"),
    (r"免费停车", "免费停车"),
]

_NOISE = re.compile(r"[【】\[\]「」（）()·、，,。.：:！!？?~～\-—_/|\s]+")
_DIGITS = re.compile(r"\d+(\.\d+)?")
_DATEISH = re.compile(
    r"(今日|明天|明日|后天|昨天|今日最后一天|倒计时\d*天?|已开抽|已上线|启幕|"
    r"\d+月\d+日|\d+\.\d+—?\d*\.?\d*|第[一二三四五六七八九十\d]+[轮期场次])"
)


# 不带「区/县」字样的区级简称——必须比市名更具体，否则「成都成华区券」会被
# 判成和「成都高新区券」同一活动（2026-10-13 实测误杀）。
DISTRICT_SHORT = {
    "成华", "龙泉驿", "锦江", "金牛", "新都", "温江", "郫都", "大邑",
    "射洪", "蓬溪", "大英", "九龙坡", "渝中", "江北", "万州",
}


def _specificity(word: str) -> int:
    """地名具体度：区/县/新区 结尾最具体，省市次之。"""
    base = len(word)
    if word.endswith(("区", "县", "市")) and len(word) > 2:
        base += 5
    if word.endswith(("新区", "专区")):
        base += 8
    if word in DISTRICT_SHORT:
        base += 5
    return base


def primary_place(title: str) -> str:
    """标题里最具体的地名词；没有则返回空串。"""
    hits = [w for w in PLACE_WORDS if w in title]
    if not hits:
        return ""
    return max(hits, key=_specificity)


def extract_acts(title: str) -> set:
    """标题里的活动类型 token 集合。"""
    acts = set()
    for pat, token in ACT_SYNONYMS:
        if re.search(pat, title):
            acts.add(token)
    return acts


def normalize_title(title: str) -> str:
    """去掉标点、数字、日期话术后的标题骨架，用于相似度兜底。"""
    t = _DATEISH.sub("", title)
    t = _NOISE.sub("", t)
    t = _DIGITS.sub("", t)
    return t.strip().lower()


def _place_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # 一方包含另一方（"遂宁" vs "遂宁市"/"安居区"）：仅在包含方向合理时算匹配
    return a in b or b in a


# 活动专名：引号/书名号里的项目名，如「蜀里安逸·遂心遂意」「宠客九条」
_BRAND = re.compile(r"[「『【】\"'](.{3,20}?)[」』【】\"']")

# 弱匹配（地域+类型交集）时的相似度门槛。
# 实测：真重复 0.47~0.81，误判 0.31 以下，0.40 可干净分离。
WEAK_SIM_THRESHOLD = 0.40

# 过度通用的活动类型：几乎任何消费类活动都命中，不能拿它当「同一活动」的证据。
# 例：成都成华区即时零售券 vs 成都高新区金秋券 —— 都是「消费券」，但是两个活动。
# 这类 token 只在弱匹配时被排除，不影响 extract_acts() 对外的完整输出。
GENERIC_ACT_TOKENS = {"消费券", "补贴"}

# 结果型 token：只描述「奖品是什么」，不描述「活动是什么」。
# 「都在送红包」不能证明是同一个活动。
# ⚠️ 2026-11-03 误杀：广西药膳大赛百问百答答题 vs 广西气象防灾减灾知识竞答，
#    同省（广西）+ 类型交集只剩「红包」+ 相似度 0.50，被误判成同一活动而整篇丢掉。
RESULT_TOKENS = {"红包", "免单", "立减金", "会员权益", "补贴", "通信福利"}

# 仅结果型 token 有交集时，要求更高的标题相似度才判重（保守：宁可漏判不可误杀）。
RESULT_ONLY_SIM = 0.62


# 通用品牌/口号：全省全市活动都在用，出现相同不代表同一个活动。
# 例：熊猫消费节 vs 「蜀里安逸」汽车消费券 —— 都带「蜀里安逸」但是两回事。
BRAND_STOPWORDS = {
    "蜀里安逸", "熊猫消费节", "惠享遂宁", "购在天府", "遂心遂意", "遂心遂宁",
    "双节", "中秋国庆", "迎中秋", "庆国庆", "喜迎中秋", "欢庆国庆",
}


def extract_brands(title: str) -> set:
    """提取标题里的活动专名（引号内项目名），排除通用品牌口号。"""
    return {
        m.strip() for m in _BRAND.findall(title)
        if len(m.strip()) >= 3 and m.strip() not in BRAND_STOPWORDS
    }


def title_sim(title_a: str, title_b: str) -> float:
    """两条标题归一化后的相似度（0~1）。

    对 `normalize_title()` 的一层封装，供 process_articles 等外部模块复用，
    保证「同一套归一化口径」只有一个实现。
    """
    na, nb = normalize_title(title_a), normalize_title(title_b)
    return difflib.SequenceMatcher(None, na, nb).ratio() if (na and nb) else 0.0


def is_same_activity(title_a: str, title_b: str) -> bool:
    """判断两条标题是否指向同一个活动。"""
    sim = title_sim(title_a, title_b)

    # 1. 标题高度相似 → 同一活动
    if sim >= 0.8:
        return True

    # 2. 活动专名相同（「蜀里安逸·遂心遂意」「宠客九条」这类）→ 强信号，直接判重
    #    允许一方包含另一方：「何以文明」与「何以文明 要德遂宁」是同一活动，
    #    但「立冬第一杯」与「0植脂末」不是（2026-11-03 补充）。
    for x in extract_brands(title_a):
        for y in extract_brands(title_b):
            if x == y or x in y or y in x:
                return True

    # 3. 弱匹配：主地域相同 + 活动类型有交集 + 相似度过门槛
    pa, pb = primary_place(title_a), primary_place(title_b)
    if not _place_match(pa, pb):
        return False
    shared = (extract_acts(title_a) & extract_acts(title_b)) - GENERIC_ACT_TOKENS
    if not shared:
        return False
    # 只剩「红包/免单/立减金」这类奖品形式相同时，相似度要更高才算同一活动
    if shared <= RESULT_TOKENS:
        return sim >= RESULT_ONLY_SIM
    return sim >= WEAK_SIM_THRESHOLD


def explain(title_a: str, title_b: str) -> str:
    """给出去重判定理由（调试/汇报用）。"""
    na, nb = normalize_title(title_a), normalize_title(title_b)
    sim = difflib.SequenceMatcher(None, na, nb).ratio() if (na and nb) else 0.0
    return (
        f"主地域={primary_place(title_a)!r}~{primary_place(title_b)!r} "
        f"类型交集={sorted(extract_acts(title_a) & extract_acts(title_b))} 相似度={sim:.2f}"
    )


if __name__ == "__main__":
    pairs = [
        ("遂宁市商务局公告：「蜀里安逸·遂心遂意」第二轮汽车消费券已于9月22日申领完毕、活动终止",
         "遂宁市「蜀里安逸·遂心遂意」消费券与「马上入川·遂心遂宁」文旅体消费券"),
        ("遂宁双节「宠客九条」发布：1000万元综合消费券 + 18家A级景区免费 + 万张砂锅券",
         "遂宁双节「宠客九条」：发放1000万元综合消费券，18家A级景区免费开放"),
        ("【倒计时6天】四川省级有奖发票活动9月30日结束：遂宁在内，单张发票最高奖1万元",
         "四川省有奖发票活动已上线：14市州开展，消费索票可抽万元大奖"),
        ("【明日开抢】遂宁安居区双节消费券：餐饮住宿/零售券9月25日起每天10点抢，自行车券仅9月25日一次",
         "遂宁市「蜀里安逸·遂心遂意」消费券与「马上入川·遂心遂宁」文旅体消费券"),
        ("遂宁工会「暖心餐」免费早餐：川工之家APP每日限领1张早餐券，线下驿站扫码核销",
         "遂宁安居工会消费券4800张：川工之家APP每日12:30/18:30放券，满100减30、满30减10"),
        # 以下应为 False（不同活动，只是同地域同类型）
        ("遂宁安居工会消费券4800张：川工之家APP每日12:30/18:30放券，满100减30、满30减10",
         "遂宁双节「宠客九条」：发放1000万元综合消费券，18家A级景区免费开放"),
        ("成都中秋国庆消费券：大邑满301减300、餐饮/住宿/景区券齐发，成华龙泉驿汽车券9月24日开抢",
         "天府新区成都直管区第二波消费券、现金红包（9.25起）"),
        ("第五届熊猫消费节9月22日启幕至11月30日：超6亿元「蜀里安逸」商贸服务消费券等你抢",
         "遂宁双节消费券全指南：40场活动+1000万元，汽车最高补1万、零售满减30%"),
        # 2026-10-13 实战误杀回放：同市不同区 / 同市不同品类的消费券是两个活动
        ("成都成华区500万元「金秋电商·即时零售」消费券（10.12-10.31）：满88减10、满128减20、满198减40",
         "成都高新区50万金秋消费券第二轮（10.9-10.15）：满100减30、满200减60、满500减150、满800减240"),
        ("成都电影消费券第二轮（10.11-10.31）：满50减25、满25减10，覆盖40家影院",
         "成都高新区50万金秋消费券第二轮（10.9-10.15）：满100减30、满200减60、满500减150、满800减240"),
    ]
    for a, b in pairs:
        print(is_same_activity(a, b), "|", a[:26], "||", b[:26], "|", explain(a, b))
