"""
公众号福利推文·主题分类桶定义（唯一真相源）

process_articles.py / region_filter.py / export_urls.py 三处共用，
避免「加一个分类要改三个文件、漏改一处导致标签显示不出来」。

用户 2026-10-09 要求：福利、红包、答题、抽奖、送礼等关联内容都要整理，
所以 gift（送礼/实物礼品）独立成桶并排在最前。

用户 2026-10-13 追问「川内票根有奖活动怎么没有统计」，故新增 ticket（票根）
独立成桶——票根经济是四川省级长期政策（至 2027-03-31），会持续产出活动，
混在「优惠」里看不出来。
"""
from collections import OrderedDict

# 分类桶：key -> (emoji + 中文名, 简介)
CATEGORY_LABELS = OrderedDict([
    ("gift",       ("🎁 送礼", "实物礼品 / 赠品 / 周边 / 试用装 / 免单")),
    ("ticket",     ("🎟️ 票根", "票根经济 / 票根惠民 / 凭证换折扣 / 有奖发票")),
    ("red_packet", ("🧧 红包", "现金红包 / 立减金 / 补贴 / 返现")),
    ("lottery",    ("🎰 抽奖答题", "抽奖 / 答题竞答 / 点赞抽奖 / 开奖")),
    ("welfare",    ("🎫 福利", "会员权益 / 免费领取 / 礼包 / 赠送")),
    ("discount",   ("💰 优惠", "消费券 / 满减 / 折扣 / 秒杀")),
    ("other",      ("📌 其他", "未归类的活动")),
])

# 展示顺序
CAT_ORDER = list(CATEGORY_LABELS.keys())

# 简短标签（表格/CSV 用，取 emoji + 名称）
SHORT_LABELS = {k: v[0] for k, v in CATEGORY_LABELS.items()}


def label_of(cat: str, with_emoji: bool = True) -> str:
    """取分类展示名，未命中返回兜底。"""
    emoji_name = CATEGORY_LABELS.get(cat, CATEGORY_LABELS["other"])[0]
    if with_emoji:
        return emoji_name
    return emoji_name.split()[-1] if " " in emoji_name else emoji_name
