# 行情、AI 与部署配置

## 离线样本

`demo/daily_prices.csv` 包含两只标的的合成日线，由 `python -m scripts.fetch_demo_data` 生成。末日参考价和最近 60 笔日收益年化波动率校准至项目的腾讯和微软历史样例。公司行动、期权链和发行商原报价由公司在授权渠道维护。联网获取的记录和真实报价存于忽略跟踪的 `private/` 与 `var/`。

## 内部 OpenBB 模式

安装锁定的 OpenBB V4 适配器。用 [市场假设样板](../demo/market_assumptions.json) 创建 `private/assumptions.json`，填入拟估值日期、该日期的币种连续利率、预期股息率和来源。估值日与假设日期必须一致，文件按日期更新。

```sh
uv sync --locked --extra openbb
uv run --locked --extra openbb python -m scripts.fetch_demo_data --openbb XHKG:00700 --as-of 2026-11-02
```

适配器分别获取仅拆股调整和股息复权收盘。首版要求最近 61 个已完整收盘的交易日齐全。历史行情错误返回 503，系统提示缺失数据或配置。OpenBB 会在运行账户的用户目录创建配置和行情缓存。网页使用 OpenBB 时设置 `FCN_MARKET_MODE=openbb` 和 `FCN_ALLOW_RESEARCH_DATA=1`，关闭演示账号并创建内部用户。 [OpenBB V4 行情接口](https://docs.openbb.co/odp/v4/python/reference/equity/price/historical/) · [V5 迁移说明](https://docs.openbb.co/odp/python/migration-from-v4)

## 授权行情接口

将公司获授权的行情适配器配置为 `FCN_VENDOR_URL`，服务端凭证配置为 `FCN_VENDOR_TOKEN`。当前接口约定为 HTTPS GET，查询参数包括 `instrument`、`start`、`end`（日期含首尾）。响应形如以下示例，实际需要返回完整窗口。

```json
{
  "instrument_id": "XHKG:00700",
  "currency": "HKD",
  "source": "公司授权行情供应商",
  "rows": [{"date": "2026-11-02", "close": 392.20, "adjusted_close": 392.20}]
}
```

`close` 为估值时点可用的收盘参考价，`adjusted_close` 为一致的复权序列。公司适配层负责合并拆股与分红。利率和预期股息输入以 `FCN_ASSUMPTIONS_FILE` 配置，字段结构见演示假设文件。客户部署前确认数据许可覆盖后台估值、缓存、前端展示和文件导出。 [港交所数据授权入口](https://www.hkex.com.hk/Services/Market-Data-Services/Real-Time-Data-Services/Data-Licensing/HKEX-IS/Market-Data-End_User-Licence/Requirements%2C-Application-Procedures_Forms?sc_lang=en)

## AI 条款草稿

将 `OPENAI_API_KEY` 与有 Structured Outputs 权限的 `OPENAI_MODEL` 配在服务器环境。模型收到客户输入的条款文字和支持的标的列表，按 JSON Schema 返回草稿。页面提交前要求客户核对结果。服务端按确定的流程获取行情与计算，API Key 保留在服务器。只在公司允许将条款描述发送给 API 的场景开启，表单始终可用。 [OpenAI Structured Outputs 文档](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)

## 账号与部署

```sh
uv run --locked python -m scripts.manage_users customer01
```

以 `FCN_DEMO_LOGIN=0` 启动。设置至少 32 字符的随机 `FCN_SECRET_KEY`，HTTPS 环境设置 `FCN_SECURE_COOKIE=1`。密码交互输入，服务端只存哈希，更新账号文件后重启服务。公司网关负责 HTTPS、登录和估值请求限流、健康检查与访问审计。部署前由公司确认实际条款、估值模型、数据授权和客户访问范围。容器的 `private/` 映射只读，运行账号需要可读取配置文件，`var/` 数据卷需支持 SQLite WAL。用 SQLite 在线备份方式保存数据库，并定期清理过期草稿与按公司要求保留的估值记录。

## 发行商报价导入

后台人员按已有估值编号关联发行商报价，确认条款和计息口径后运行 `uv run --locked python -m scripts.import_issuer_quotes private/quotes.json`。输入为 JSON 数组，每条包含 `valuation_id`、`issuer`、`quoted_at`、`expires_at`、`basis`（`annual` 或 `full_term`）、`coupon`（小数，0.0622 表示 6.22%）。时间戳带时区。只接受授权行情模式的已完成估值，有效期内且条款完全匹配才展示。
