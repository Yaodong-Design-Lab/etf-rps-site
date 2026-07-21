#!/usr/bin/env python3
import argparse
import re
from pathlib import Path

import pandas as pd


COMPANY_WORDS = [
    "华夏",
    "易方达",
    "广发",
    "国泰",
    "万家",
    "鹏华",
    "华泰柏瑞",
    "博时",
    "招商",
    "南方",
    "汇添富",
    "富国",
    "嘉实",
    "华安",
    "天弘",
    "景顺长城",
    "工银",
    "工银瑞信",
    "银华",
    "建信",
    "平安",
    "摩根",
    "大成",
    "永赢",
    "中银",
    "兴业",
    "浦银安盛",
    "华宝",
    "国联安",
    "华富",
    "东财",
    "长城",
    "泰康",
    "大成",
    "国投瑞银",
]

BROAD_INDUSTRY_RULES = [
    ("半导体", r"半导体|芯片"),
    ("信息技术", r"信息技术|人工智能|AI|科技传媒"),
    ("通信", r"5G|通信"),
    ("银行", r"银行"),
    ("非银金融", r"证券|券商|金融|非银|保险"),
    ("煤炭", r"煤炭"),
    ("新材料", r"新材料"),
    ("科创成长", r"科创板成长|科创创业|创业板成长|成长100"),
    ("电网设备", r"电网设备"),
    ("黄金", r"黄金"),
    ("有色金属", r"有色|稀有金属"),
    ("医药", r"创新药|生物医药|生物科技|疫苗|医疗|医药|中药"),
    ("汽车", r"汽车"),
    ("旅游", r"旅游"),
    ("电池", r"电池"),
    ("能源化工", r"能源化工|油气|石化|化工"),
    ("军工", r"军工|航空|航天"),
    ("消费", r"消费"),
    ("红利", r"红利"),
]

SUB_THEME_RULES = [
    ("港股医药", r"恒生.*(医药|医疗|创新药)|香港.*医药|港股.*(医药|医疗|创新药)|港股通.*(医药|医疗|创新药)"),
    ("海外生物科技", r"标普.*生物科技|纳指.*生物科技"),
    ("中药", r"中药"),
    ("创新药", r"创新药"),
    ("生物医药", r"生物医药|生物科技|疫苗|医疗"),
]

THEME_RULES = [
    ("半导体材料设备", r"半导体材料设备|科创板半导体材料设备"),
    ("半导体产业", r"半导体产业"),
    ("科创芯片", r"科创板芯片|科创芯片"),
    ("港股通信息技术", r"港股通信息技术"),
    ("5G通信", r"5G|通信主题|通信服务|国证通信"),
    ("煤炭", r"煤炭"),
    ("科创新材料", r"科创板新材料|新材料"),
    ("科创成长", r"科创板成长"),
    ("银行", r"银行"),
    ("证券", r"证券|券商"),
    ("人工智能", r"人工智能|AI"),
    ("电网设备", r"电网设备"),
    ("黄金", r"黄金"),
    ("有色金属", r"有色|稀有金属"),
    ("创新药", r"创新药"),
    ("生物医药", r"生物医药|生物科技|疫苗"),
    ("港股汽车", r"港股.*汽车|汽车"),
    ("旅游", r"旅游"),
    ("电池", r"电池"),
    ("油气", r"油气|石化"),
    ("军工", r"军工|航空|航天"),
    ("消费", r"消费"),
    ("红利", r"红利"),
    ("金融", r"金融|非银|保险"),
    ("创业板成长", r"创业板成长"),
    ("成长100", r"成长100"),
    ("科创创业50", r"科创创业50"),
]


def simplify_name(name: str) -> str:
    text = str(name)
    text = re.sub(r"\(QDII\)|（QDII）|ETF联接|交易型开放式指数证券投资基金", "", text)
    for word in sorted(COMPANY_WORDS, key=len, reverse=True):
        text = text.replace(word, "")
    text = text.replace("中证", "").replace("上证", "").replace("国证", "")
    text = text.replace("主题", "").replace("股票", "")
    text = re.sub(r"ETF.*$", "ETF", text)
    text = re.sub(r"基金$", "ETF", text)
    text = re.sub(r"\s+", "", text)
    return text or str(name)


def theme_of(name: str) -> str:
    text = str(name)
    for industry, pattern in BROAD_INDUSTRY_RULES:
        if re.search(pattern, text, flags=re.I):
            return industry
    simple = simplify_name(text)
    simple = simple.replace("ETF", "")
    return simple[:12] or text[:12]


def sub_theme_of(name: str) -> str:
    text = str(name)
    for sub_theme, pattern in SUB_THEME_RULES:
        if re.search(pattern, text, flags=re.I):
            return sub_theme
    return theme_of(text)


def dedupe_by_theme(df: pd.DataFrame, sort_cols: list[str], n: int) -> pd.DataFrame:
    ranked = df.sort_values(sort_cols, ascending=[False] * len(sort_cols)).copy()
    ranked["theme"] = ranked["name"].map(theme_of)
    ranked["short_name"] = ranked["name"].map(simplify_name)
    ranked = ranked.drop_duplicates("theme", keep="first")
    return ranked.head(n)


def fmt_table(df: pd.DataFrame, cols: list[str]) -> str:
    out = df[cols].copy()
    rename = {
        "code": "代码",
        "short_name": "名称",
        "theme": "主题",
        "rps3": "rps3",
        "rps5": "rps5",
        "rps10": "rps10",
        "rps20": "rps20",
        "rps50": "rps50",
        "rps120": "rps120",
        "rps250": "rps250",
        "ret1": "比前一天",
        "ret3": "比3天前",
        "ret5": "比5天前",
        "ret10": "比10天前",
        "ret20": "比20天前",
    }
    out = out.rename(columns=rename)
    return out.to_markdown(index=False, floatfmt=".1f")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a deduped ETF daily observation report.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--date", default="2026-06-12")
    parser.add_argument("--top-long", type=int, default=10)
    parser.add_argument("--top-short", type=int, default=10)
    parser.add_argument("--top-rps20", type=int, default=8)
    parser.add_argument("--out-dir", default="output/etf_rps_real_nav")
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    for col in ["rps3", "rps5", "rps10", "rps20", "rps50", "rps120", "rps250"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["ret1", "ret3", "ret5", "ret10", "ret20"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    long_df = dedupe_by_theme(df.dropna(subset=["rps50", "rps120", "rps250"]), ["rps250", "rps120", "rps50"], args.top_long)
    short_df = dedupe_by_theme(df.dropna(subset=["rps3", "rps5", "rps10"]), ["rps10", "rps5", "rps3"], args.top_short)
    rps20_df = dedupe_by_theme(df.dropna(subset=["rps20"]), ["rps20", "rps5"], args.top_rps20)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"daily_observation_dedup_{args.date}.md"
    xlsx_path = out_dir / f"daily_observation_dedup_{args.date}.xlsx"

    sections = [
        f"# 每日观察 ETF（行业排重版）- {args.date}",
        "## 长周期观察：rps50 / rps120 / rps250",
        fmt_table(
            long_df,
            ["code", "short_name", "theme", "rps50", "rps120", "rps250", "ret1", "ret3", "ret5", "ret10", "ret20"],
        ),
        "## 短周期观察：rps3 / rps5 / rps10",
        fmt_table(
            short_df,
            ["code", "short_name", "theme", "rps3", "rps5", "rps10", "ret1", "ret3", "ret5", "ret10", "ret20"],
        ),
        "## rps20 轮动观察",
        fmt_table(
            rps20_df,
            ["code", "short_name", "theme", "rps20", "rps5", "ret1", "ret3", "ret5", "ret10", "ret20"],
        ),
        "",
        "> 口径：同一行业/主题只保留当前排序维度最强的一只；名称已去掉基金公司、指数长前缀和冗余后缀。",
    ]
    md_path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")

    with pd.ExcelWriter(xlsx_path) as writer:
        long_df.to_excel(writer, sheet_name="长周期", index=False)
        short_df.to_excel(writer, sheet_name="短周期", index=False)
        rps20_df.to_excel(writer, sheet_name="rps20", index=False)

    print(md_path)
    print(xlsx_path)


if __name__ == "__main__":
    main()
