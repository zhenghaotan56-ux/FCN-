"""Render the saved valuation and terms as a customer PDF."""

from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))
NAVY = colors.HexColor("#102A43")
INK = colors.HexColor("#334E68")
PALE = colors.HexColor("#E9F3FA")


def quote_pdf(record):
    terms, market, result = (record[key] for key in ("terms", "market", "result"))
    name = record["instrument"]["name"]
    stream = BytesIO()
    document = SimpleDocTemplate(
        stream, pagesize=A4, leftMargin=43, rightMargin=43, topMargin=40, bottomMargin=35,
        title=f"FCN {record['id'][:12]}",
    )
    body = ParagraphStyle("body", fontName=FONT, fontSize=10.5, leading=16, textColor=INK, wordWrap="CJK")
    title = ParagraphStyle("title", parent=body, fontSize=25, leading=32, textColor=NAVY)
    section = ParagraphStyle("section", parent=body, fontSize=14, leading=20, textColor=NAVY)

    def para(text, style=body):
        return Paragraph(escape(str(text)), style)

    def table(rows, widths):
        result_table = Table([[para(value) for value in row] for row in rows], colWidths=widths, repeatRows=1)
        result_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PALE),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F8FB")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LINEBELOW", (0, 0), (-1, 0), .7, colors.HexColor("#B9D3E7")),
        ]))
        return result_table

    def stock_price(value):
        whole, fraction = f"{value:,.6f}".split(".")
        return f"{whole}.{fraction[:2]}{fraction[2:].rstrip('0')}"

    ko = "无敲出" if terms["ko"] is None else f"每月敲出 {stock_price(terms['ko'])}"
    price_kind = "盘中最新成交价" if market.get("price_kind") == "realtime" else "参考收盘价"
    price_at = market.get("price_at", market["as_of"]).replace("T", " ")
    band = f"{result['range'][0]:.2%} – {result['range'][1]:.2%}"
    story = [
        para(f"固定票息票据 · {terms['tenor_months']} 个月估值", title),
        para(f"{name}  {terms['instrument_id']}  |  {terms['currency']}  |  估值日 {market['as_of']}"),
        para(f"估值编号 {record['id']}"),
        Spacer(1, 14),
        table([
            ["估算年化票息", "持有至到期票息比例", "参数敏感区间"],
            [f"{result['annual_coupon']:.2%}", f"{result['full_term_coupon']:.2%}", band],
        ], [169, 169, 170]),
        Spacer(1, 13),
        para("已确认条款", section),
        para(f"{price_kind} {stock_price(terms['reference_price'])} · 接货价 {stock_price(terms['strike'])} "
             f"（{terms['strike_pct']:.2f}%） · {ko} · 面值 {terms['notional']:,.2f} {terms['currency']}"),
        para(f"行情时点（交易所当地） {price_at} · 历史行情截至 {market.get('history_end', market['as_of'])}"),
        para(f"观察日 {' / '.join(event['observation'] for event in terms['events'])}。"
             "无敲入，存续期固定计息，终止时一次付息，ACT/365F。"),
        Spacer(1, 12),
        para(f"结算情景 · {terms['currency']}", section),
        table([
            ["情景", "计息天数", "票息", "本金或股票等值", "净损益"],
            *[[row["label"], row["days"], f"{row['coupon']:,.2f}",
               f"{row['principal_value']:,.2f}", f"{row['net']:,.2f}"] for row in result["scenarios"]],
        ], [177, 55, 82, 110, 84]),
        Spacer(1, 12),
        para("估值口径", section),
        para(f"{market['source']}。{market['assumption_source']}。"
             f"年化历史波动率 {market['volatility']:.2%}（{market['volatility_observations']} 笔日收益），"
             f"连续利率 {market['rate']:.2%}，预期股息率 {market['dividend_yield']:.2%}。"),
        para(f"{result['method']} · {result['model']} · {terms['version']}。"
             f"区间采用{result['range_method']}。"),
    ]
    if result["standard_error"] is not None:
        story.append(para(f"模拟路径 {result['paths']:,} 条，种子 {result['seed']}，"
                          f"年化票息模拟标准误 {result['standard_error'] * 10000:.2f} 个基点。"))
    story.extend([
        Spacer(1, 7),
        para("票息为模型估算。实际发行报价及支付、交割安排以发行商最终条款为准。"
             "股票交割等值按情景估值日股价计算，实际交付股数及零碎股按发行条款处理。"),
    ])
    document.build(story)
    stream.seek(0)
    return stream
