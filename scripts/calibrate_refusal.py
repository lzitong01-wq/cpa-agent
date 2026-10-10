# -*- coding: utf-8 -*-
"""拒答阈值标定脚本。

答疑模块必须做到：教材里有的能答、教材里没有的必须回"CPA教材中没有对应相关描述"。
这两件事由 config.RELEVANCE_THRESHOLD（稠密余弦）与 config.SPARSE_PER_TOKEN_MIN
（BM25 按词均值）两个绝对阈值控制，任一达标即认为检索到了教材内容。

本脚本用两组带标签的问题跑一遍检索，输出：
- 每条问题的 dense / bm25 / bm25按词均值；
- 当前阈值下的判定结果与错误数；
- 在两组分数之间搜索"完全可分"的推荐阈值。

更换教材语料或嵌入模型后，跑一次本脚本并把推荐值写回 config.py 即可。
用法：
    python scripts/calibrate_refusal.py
    python scripts/calibrate_refusal.py --quiet     # 只看汇总
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from app import db  # noqa: E402
from app.rag import bm25 as bm25_mod, retriever  # noqa: E402

# 范围内：教材里确实写了的问题（含极短查询、口语化提问、换一种说法）
IN_SCOPE = [
    "什么是暂时性差异", "存货跌价准备怎么计提", "可变现净值的定义",
    "收入确认的五步法是哪五步", "重要性水平怎么确定", "审计风险模型是什么",
    "什么时候发表保留意见", "杜邦分析体系的核心公式", "加权平均资本成本怎么算",
    "内含报酬率和净现值冲突怎么办", "增值税进项税额哪些不能抵扣", "业务招待费的扣除限额",
    "专项附加扣除有哪几项", "抵押权什么时候设立", "股东会特别决议需要多少表决权",
    "操纵证券市场有哪些情形", "五力模型包括哪些", "内部控制五要素",
    "PEST分析里人口老龄化属于哪一类", "可撤销的民事法律行为有哪些",
    "老师这个递延所得税资产到底啥时候确认啊", "为啥速动比率要把存货扣掉",
    "小规模纳税人的征收率是多少",
    "暂时性差异", "五力模型", "WACC", "留置权", "表见代理", "可变现净值", "职业怀疑",
    "存货", "递延所得税负债怎么确认", "属性抽样", "二叉树期权定价", "前向一体化",
]

# 范围外：必须触发拒答（含"看起来像 CPA 但教材没写"的干扰项）
OUT_SCOPE = [
    "薛定谔方程怎么推导", "怎么用Python写一个爬虫", "今天天气怎么样", "红楼梦的作者是谁",
    "推荐几个减肥食谱", "英语四级怎么备考", "篮球比赛一场多少分钟", "医保报销比例是多少",
    "如何办理护照", "光合作用的过程", "刑法里故意杀人罪怎么判", "会计从业资格证怎么报名",
    "帮我写一封辞职信", "上海到北京高铁多久", "微积分的洛必达法则", "怎么做红烧肉",
    "注会考试报名费多少钱", "我该先考哪一科",
]

def measure(queries):
    rows = []
    for q in queries:
        r = retriever.search(q, log_it=False)
        n = max(1, len(bm25_mod.tokenize(q)))
        rows.append({"query": q, "dense": r["dense_top"], "bm25": r["sparse_top"],
                     "per_token": r["sparse_top"] / n, "ntok": n,
                     "refused": r["refused"]})
    return rows


def _suggest(in_vals, out_vals, floor):
    """在范围外最高分与范围内最低分之间取中点作为推荐阈值。"""
    hi_out = max(out_vals) if out_vals else 0.0
    lo_in = min(in_vals) if in_vals else 0.0
    if lo_in > hi_out:  # 单信号即可完全分开
        return round((hi_out + lo_in) / 2, 3), True
    return round(max(hi_out * 1.1, floor), 3), False


def main():
    ap = argparse.ArgumentParser(description="拒答阈值标定")
    ap.add_argument("--quiet", action="store_true", help="只输出汇总")
    args = ap.parse_args()

    db.init_db()
    ins, outs = measure(IN_SCOPE), measure(OUT_SCOPE)

    if not args.quiet:
        for title, rows in (("范围内（应作答）", ins), ("范围外（应拒答）", outs)):
            print("\n--- %s ---" % title)
            print("dense   bm25    /词    词数  判定    问题")
            for r in rows:
                print("%.4f  %-7.2f %-6.2f %-5d %-7s %s"
                      % (r["dense"], r["bm25"], r["per_token"], r["ntok"],
                         "拒答" if r["refused"] else "作答", r["query"]))

    fn = [r for r in ins if r["refused"]]       # 该答却拒答（漏答）
    fp = [r for r in outs if not r["refused"]]  # 该拒却作答（幻觉风险）
    print("\n=== 当前阈值 relevance=%.2f  sparse_per_token=%.2f ==="
          % (config.RELEVANCE_THRESHOLD, retriever.SPARSE_PER_TOKEN_MIN))
    print("范围内 %d 条，误拒 %d 条；范围外 %d 条，误答 %d 条"
          % (len(ins), len(fn), len(outs), len(fp)))
    for r in fn:
        print("  [误拒] %s（dense %.4f，/词 %.2f）" % (r["query"], r["dense"], r["per_token"]))
    for r in fp:
        print("  [误答] %s（dense %.4f，/词 %.2f）" % (r["query"], r["dense"], r["per_token"]))

    print("\n=== 分数分布 ===")
    print("范围内  dense [%.4f, %.4f]   /词 [%.2f, %.2f]"
          % (min(r["dense"] for r in ins), max(r["dense"] for r in ins),
             min(r["per_token"] for r in ins), max(r["per_token"] for r in ins)))
    print("范围外  dense [%.4f, %.4f]   /词 [%.2f, %.2f]"
          % (min(r["dense"] for r in outs), max(r["dense"] for r in outs),
             min(r["per_token"] for r in outs), max(r["per_token"] for r in outs)))

    # 两个信号是 OR 关系：先用 dense 卡掉范围外，再用 /词 把 dense 偏低的范围内问题捞回来
    d_gate = round(max(r["dense"] for r in outs) * 1.12, 3)
    rescued = [r for r in ins if r["dense"] < d_gate]
    p_out = max(r["per_token"] for r in outs)
    p_in = min((r["per_token"] for r in rescued), default=None)
    print("\n=== 推荐阈值 ===")
    print("CPA_RELEVANCE_THRESHOLD    = %.2f   （范围外最高 dense %.4f 上浮 12%%）"
          % (d_gate, max(r["dense"] for r in outs)))
    if p_in is None:
        print("CPA_SPARSE_PER_TOKEN_MIN   = %.2f   （dense 单信号已覆盖全部范围内问题）"
              % round(p_out * 1.2, 2))
    elif p_in > p_out:
        print("CPA_SPARSE_PER_TOKEN_MIN   = %.2f   （需靠 BM25 捞回 %d 条：范围外最高 %.2f "
              "< 待捞回最低 %.2f）" % (round((p_in + p_out) / 2, 2), len(rescued), p_out, p_in))
    else:
        print("CPA_SPARSE_PER_TOKEN_MIN   = 无法完全可分：待捞回最低 %.2f <= 范围外最高 %.2f，"
              "建议扩充语料或更换嵌入模型" % (p_in, p_out))
    return 1 if (fn or fp) else 0


if __name__ == "__main__":
    sys.exit(main())

