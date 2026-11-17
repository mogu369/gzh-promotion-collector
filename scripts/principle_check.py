"""
收录第一性原理自检（2026-11-17 用户定，统摄全篇的最高判据）

用户原话：「检索最基本的要求是能够让人免费或者低成本参与，
并收获钱、福利、官方优惠，按照这个基础分析过往内容并补充规则。」

两条必须同时成立的门槛：
  A 参与成本 = 免费 或 低成本（不是高成本/变相门槛）
  B 收获 = 钱 / 福利 / 官方优惠（三者居其一，纯资讯/纯预告不算）

与 region_filter.usability_block_reason 的分工：
  - region_filter 管「可参与性」：定位/到现场/身份/下载APP/实名/集赞/入口未开
  - 本模块管「第一性原理」：成本高到变相门槛（办贷/开户/刷脸授信/跑网点）、
    以及「没有实际权益」的纯宣传/纯预告。两者互补，不重复。

用法：
  from principle_check import principle_block_reason
  reason = principle_block_reason(article)   # 返回空串=过，否则=拦截原因

  python scripts/principle_check.py          # 跑自测
"""
import re

# ---- 门槛 A：高成本 / 变相门槛（收权益要付出 disproportionate 代价）----
# 注意与 region_filter 的下载APP/实名/集赞/加人区分；这里管「钱与信用与线下跑腿」
_RE_HIGH_COST = re.compile(
    r"申请(?:贷款|借款|分期|额度)|办理(?:贷款|借款|信用卡|证券账户|开户)|"
    r"(?:贷款|借款)[^。；]{0,8}(?:额度|申请)|"
    r"上传身份证[^。；]{0,10}(?:刷脸|认证|授信)|刷脸[^。；]{0,8}(?:授信|贷款|申请)|"
    r"开通[^。；]{0,6}(?:证券|基金|理财)[^。；]{0,6}账户|"
    r"(?:到|前往|亲临)(?:网点|柜台)[^。；]{0,8}(?:办理| activated|激活|领取)|跑网点"
)
# 否定语境：不办贷款/无需到网点 = 零门槛，不拦（同 region_filter 的 _find_positive 口径）
_RE_HIGH_COST_NEG = re.compile(r"无需|无须|不用|不必|不需|勿需|不需要|不用去|不必到")

# 高成本句也可能是「另一个活动」的顺带说明（如银行线报汇总里提了句邮享贷），
# 只看是否为主语动作——这里保守：命中即拦，交给人工在 raw 阶段拆条。

# ---- 门槛 B：收获维度——必须有实际权益 ----
# 权益「否定 / 未定」信号：命中即视为尚无实际可拿的权益。
# 必须在下面的正向权益信号**之前**判定——否则「无抽奖无优惠」会因含
# 「抽奖/优惠」被正向信号误判为有权益（2026-11-17 自测踩坑）。
_RE_NO_ACTUAL_BENEFIT = re.compile(
    r"无(?:任何)?(?:抽奖|优惠|福利|奖品|活动)|"
    r"(?:奖品|奖励|规则|细则|具体安排|礼品)[^。；]{0,4}(?:待公布|待定|另行通知|尚未公布)|"
    r"敬请期待|以官方后续公告为准|"
    r"(?:发布|推出)(?:新款|新品|新作)[^。；]{0,12}(?:介绍|卖点|参数|亮相)"
)
# 纯通知/纯资讯开头（且没有任何权益词）
_RE_NO_BENEFIT = re.compile(
    r"^(?:关于|通知|公告)[^。；]{0,20}(?:发布|上线|启动)"
)
# 权益信号：命中任一即认为「有收获」
_RE_BENEFIT_SIGNAL = re.compile(
    r"红包|现金|提现|零钱|话费|立减金|刷卡金|返现|奖励金|补贴|"
    r"抽|奖|赠|送|免费|免单|优惠|折扣|满减|消费券|立减|抵扣|"
    r"券|卡|票|礼品|实物|周边|文创|纪念|礼包|权益|福利|惠民|公益|"
    r"免票|优惠价|特价|秒杀|0元|一元购|1分|9\.9|报销|领取|兑换"
)


def _find_positive(regex, text):
    for m in regex.finditer(text):
        prefix = text[max(0, m.start() - 8):m.start()]
        if _RE_HIGH_COST_NEG.search(prefix):
            continue
        return m
    return None


def principle_block_reason(article: dict) -> str:
    """返回违反第一性原理的原因；符合返回空串。

    仅做两条「脚本能可靠判定」的检查：
      1. 高成本变相门槛（门槛 A）
      2. 无任何权益信号（门槛 B）
    官方性（门槛 C）由 source_filter 负责，不在这里重复。
    """
    title = article.get("title", "") or ""
    summary = article.get("summary", "") or ""
    text = (title + " " + summary).replace("（", "(").replace("）", ")")

    # 门槛 A：高成本/变相门槛
    m = _find_positive(_RE_HIGH_COST, text)
    if m:
        return f"高成本/变相门槛，非免费或低成本参与（…{m.group(0)}…）"

    # 门槛 B：纯宣传/纯预告/纯资讯，无实际权益可收获
    m = _RE_NO_ACTUAL_BENEFIT.search(text)
    if m:
        return f"无实际权益可收获（…{m.group(0)}…）"
    if not _RE_BENEFIT_SIGNAL.search(text):
        if _RE_NO_BENEFIT.search(text) or len(text) < 30:
            return "无钱/福利/官方优惠等实际权益（纯资讯或纯宣传）"

    return ""


def _self_test():
    cases = [
        # (标题, 摘要, 是否应拦, 说明)
        ("邮储邮享贷额度申请", "上传身份证并刷脸授信申请邮享贷额度，完成后抽28元立减金", True, "办贷款+刷脸=高成本"),
        ("某证券开户有礼", "开通证券账户并完成首次交易，送100元话费", True, "开户=高成本"),
        ("建行转账预填单", "需亲临网点柜台办理后领5元立减金", True, "跑网点=高成本"),
        ("某品牌新品发布", "品牌发布新款手机，介绍参数与卖点，无抽奖无优惠", True, "纯宣传无权益"),
        ("活动预告", "活动下周开始，具体奖品待公布", True, "纯预告无权益"),
        # 不该拦
        ("建行支付达标赢好礼", "月消费满2000元抽最高88元立减金，官方达标活动", False, "低成本+官方优惠"),
        ("茂名应急答题兑现金红包", "关注公众号无需注册无需下载APP，答5题得积分兑随机微信红包", False, "零成本+钱"),
        ("光影惠蓉公益电影", "全市40家影城免费看电影，小程序预约，官方公益", False, "零成本+福利"),
        ("比亚迪购车补贴", "购车最高补1万元，以旧换新可叠加，官方补贴", False, "低成本（大额消费但官方补贴属优惠）"),
        ("邮储信用卡缴暖气费满减", "满1000减20，官方满减优惠，无需贷款", False, "否定语境不误伤"),
    ]
    ok = 0
    for t, s, expect, note in cases:
        got = bool(principle_block_reason({"title": t, "summary": s}))
        flag = "OK" if got == expect else "FAIL"
        if got == expect:
            ok += 1
        print(f"{flag} expect={expect!s:5} got={got!s:5} {t[:20]:20s} ({note})"
              + (f"  →{principle_block_reason({'title': t, 'summary': s})}" if got else ""))
    print(f"principle self-test: {ok}/{len(cases)} passed")
    return ok == len(cases)


if __name__ == "__main__":
    raise SystemExit(0 if _self_test() else 1)
