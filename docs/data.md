# 行情、AI 与部署配置

## 离线样本

`demo/daily_prices.csv` 包含两只标的的合成日线，由 `python -m scripts.fetch_demo_data` 生成。末日参考价和最近 60 笔日收益年化波动率校准至项目的腾讯和微软历史样例。公司行动、期权链和发行商原报价由公司在授权渠道维护。联网获取的记录和真实报价存于忽略跟踪的 `private/` 与 `var/`。

## 内部 OpenBB 模式

安装锁定的 OpenBB V4 适配器。用 [市场假设样板](../demo/market_assumptions.json) 创建 `private/assumptions.json`，填入拟估值日期、该日期的币种连续利率、预期股息率和来源。估值日与假设日期必须一致，文件按日期更新。

```sh
uv sync --locked --extra openbb
uv run --locked --extra openbb python -m scripts.fetch_demo_data --openbb XHKG:00700 --as-of 2026-11-02
```

适配器分别获取仅拆股调整和股息复权收盘，作为内部研究的日线模式。OpenBB 会在运行账户的用户目录创建配置和行情缓存。网页使用 OpenBB 时设置 `FCN_MARKET_MODE=openbb` 和 `FCN_ALLOW_RESEARCH_DATA=1`，关闭演示账号并创建内部用户。 [OpenBB V4 行情接口](https://docs.openbb.co/odp/v4/python/reference/equity/price/historical/)

## 长桥港美股行情

长桥适配器校验代码、交易所和币种，读取常规时段最新成交以及原始和前复权的完整日线。支持直接输入 `00700`、`00700.HK`、`AAPL` 或 `BRK.B`。覆盖取决于账户的行情权限、历史配额与标的可取得的数据。新股或停牌后的股票可能缺少足够的完整日线，可以尝试较短样本。盘中成交时间距请求最多 5 分钟，标的暂停交易或报价不够新时返回明确提示。港股午休以及收市竞价尚未结束时，使用之前的完整收盘。美国盘前、盘后和夜盘按收盘估值口径处理。 [长桥行情权限](https://open.longbridge.com/docs/quote/overview) · [长桥最新报价](https://open.longbridge.com/docs/quote/pull/quote) · [港交所交易时间](https://www.hkex.com.hk/en/Services/Trading-hours-and-Severe-Weather-Arrangements/Trading-Hours/Securities-Market?sc_lang=en)

本地使用 `uv sync --locked --extra live` 和 `uv run --locked --extra live flask --app backend.app.main:create_app run`。Docker 默认包含 `live` 依赖。长桥配置使用开发者中心创建的 Legacy API Key 凭证。账号应具有相应的行情使用许可。 [长桥凭证配置](https://open.longbridge.com/docs/getting-started) · [长桥历史日线配额](https://open.longbridge.com/docs/quote/pull/history-candlestick)

## 授权行情接口

将公司获授权的行情适配器配置为 `FCN_VENDOR_URL`，服务端凭证配置为 `FCN_VENDOR_TOKEN`，并设 `FCN_MARKET_MODE=vendor`。接口为 HTTPS GET，查询参数包括 `instrument`、`start`、`end`（日期含首尾）。普通美股输入可能发送 `US:AAPL`。指定交易所时发送完整代码，例如 `XNAS:AAPL`。盘中请求增加 `quote=realtime`，此时 `end` 仍是最近完整交易日。响应示例中的日线须替换成完整数据。

```json
{
  "instrument_id": "XHKG:00700",
  "currency": "HKD",
  "source": "公司授权行情供应商",
  "name": "腾讯控股",
  "rows": [{"date": "2026-11-02", "close": 392.20, "adjusted_close": 392.20}],
  "quote": {"price": 394.20, "timestamp": "2026-11-03T10:15:00+08:00", "realtime": true}
}
```

`close` 为完整交易日的收盘成交价，`adjusted_close` 是供计算收益率使用的复权序列。盘中必填 `quote`，时间戳须带时区，`realtime` 须为 `true`。公司适配层负责合并拆股与分红。客户部署前确认数据许可覆盖后台估值、缓存、前端展示和文件导出。 [港交所数据授权入口](https://www.hkex.com.hk/Services/Market-Data-Services/Real-Time-Data-Services/Data-Licensing/HKEX-IS/Market-Data-End_User-Licence/Requirements%2C-Application-Procedures_Forms?sc_lang=en)

## Render 开启行情和 AI

先更新包含本版本代码的 Docker 部署。在 **Environment** 中填入相应的值。密钥只填在 Render 后台。

| Key | Value |
| --- | --- |
| `FCN_MARKET_MODE` | `longbridge`，已有兼容接口可选 `vendor` |
| `FCN_DEMO_LOGIN` | `0` |
| `FCN_USERS_JSON` | 运行下方命令生成的 `private/users.json` 文件内容 |
| `FCN_ASSUMPTIONS_JSON` | 下面列出的当期利率和股息假设 JSON |
| `LONGBRIDGE_APP_KEY` | 长桥开发者中心的 App Key |
| `LONGBRIDGE_APP_SECRET` | 长桥开发者中心的 App Secret |
| `LONGBRIDGE_ACCESS_TOKEN` | 长桥 Legacy Access Token |
| `FCN_LONGBRIDGE_HK_REALTIME` | 已开通 OpenAPI 港股实时权限时填 `1` |
| `LONGBRIDGE_PRINT_QUOTE_PACKAGES` | `false` |
| `DEEPSEEK_API_KEY` | DeepSeek 开放平台生成的 API Key |
| `DEEPSEEK_MODEL` | `deepseek-flash`，可省略，程序默认使用此模型 |

在本地运行 `uv run --locked python -m scripts.manage_users boss`，交互式设置密码。`FCN_USERS_JSON` 只包含用户名和密码哈希。已有 `FCN_SECRET_KEY`、`FCN_SECURE_COOKIE=1` 和 `PORT=8000` 可继续使用。保存时选 **Save, rebuild, and deploy**。只开 AI 时可继续使用 `FCN_MARKET_MODE=fixture` 并配置独立测试账号。 [Render 环境变量](https://render.com/docs/configure-environment-variables)

`FCN_ASSUMPTIONS_JSON` 示例为 `{"rates":{"HKD":0.03,"USD":0.04},"source":"演示利率假设"}`。将数字和来源替换为公司采用的当期连续利率，`0.03` 表示 3%。可按标的在 `dividend_yields` 中填写预期股息率，例如 `{"XHKG:00700":0.01}`。长桥模式未填写时用供应商提供的每股股息除以参考价作为股息率估计。授权 HTTP 接口和 OpenBB 也可以使用显式配置的 `default_dividend_yield`。使用挂载文件时配置 `FCN_ASSUMPTIONS_FILE`，若 JSON 带 `as_of`，须与估值日一致。

## AI 条款草稿

在 Render 的 **Environment** 添加 `DEEPSEEK_API_KEY`，可以删除旧的 `OPENAI_API_KEY` 和 `OPENAI_MODEL`。保存并重新部署后，DeepSeek Flash 生成条款草稿与估值解释，服务器校验返回的 JSON。客户确认标的、期限和价格条款后，行情和票息仍由程序计算。 [DeepSeek API Key](https://platform.deepseek.com/api_keys) · [DeepSeek JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)

## 账号与部署

```sh
uv run --locked python -m scripts.manage_users customer01
```

以 `FCN_DEMO_LOGIN=0` 启动。设置至少 32 字符的随机 `FCN_SECRET_KEY`，HTTPS 环境设置 `FCN_SECURE_COOKIE=1`。密码交互输入，服务端只存哈希，更新账号文件后重启服务。公司网关负责 HTTPS、登录和估值请求限流、健康检查与访问审计。部署前由公司确认实际条款、估值模型、数据授权和客户访问范围。容器的 `private/` 映射只读，运行账号需要可读取配置文件，`var/` 数据卷需支持 SQLite WAL。用 SQLite 在线备份方式保存数据库，并定期清理过期草稿与按公司要求保留的估值记录。

## 发行商报价导入

后台人员按已有估值编号关联发行商报价，确认条款和计息口径后运行 `uv run --locked python -m scripts.import_issuer_quotes private/quotes.json`。输入为 JSON 数组，每条包含 `valuation_id`、`issuer`、`quoted_at`、`expires_at`、`basis`（`annual` 或 `full_term`）、`coupon`（小数，0.0622 表示 6.22%）。时间戳带时区。只接受授权行情模式的已完成估值，有效期内且条款完全匹配才展示。
