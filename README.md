# FCN 估值工作台

港美股单标的固定票息票据估值工具。支持 1–12 个月整月期限、无敲入、无敲出或每月敲出。波动率与股价走势支持独立回看约 1、3、6、12 个月。联网模式可直接输入股票代码。实时行情模式在正常连续交易时段取最新成交，其他时间取最近完整收盘。确认参考价、接货价与观察日后，可计算估算年化票息、全期票息和结算情景，并导出保存的估值报告。

## 本地运行

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。在仓库根目录运行以下命令。Windows 可将复制命令换为 `Copy-Item .env.example .env`。

```sh
cp .env.example .env
uv sync --locked
uv run --locked flask --app backend.app.main:create_app run --host 127.0.0.1 --port 8000
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。本地演示账号是 `demo`，密码是 `fcn-demo-only`。使用腾讯样例按钮，或切换为微软。默认日线是清楚标注的合成数据，估值日固定在 2026-11-02。无需联网、行情凭证或 AI API Key。

```sh
uv run --locked pytest
uv run --locked python -m scripts.fetch_demo_data
```

## 文件关系

```text
frontend/                    登录、表单、条款确认、走势图、估值结果
backend/app/main.py          网页与 JSON API、会话和条款确认入口
backend/app/assistant.py     可选 AI 条款草稿
backend/app/terms.py         校验、执行价换算与交易日程
backend/app/market.py        模拟、OpenBB 或授权行情及固定快照
backend/app/longbridge_feed.py  长桥港美股实时价格、证券正股资料和历史日线
backend/app/pricing.py       解析定价、路径模拟与现金流
backend/app/store.py         SQLite 持久化与按客户隔离
backend/app/report.py        读取同一估值记录生成 PDF
backend/migrations/         数据库结构
demo/                       合成日线、条款和人工路径
scripts/                    行情采集、账号与实际报价管理
tests/                      定价和 API 验收
Dockerfile / compose.yaml    私有环境部署入口
```

表单或 AI 生成草稿，预览接口校验并冻结条款和行情，估值接口凭预览编号计算。报告读取已保存的结果。AI 仅提取参数，结果由程序生成。

- [产品条款与接口](docs/terms.md)
- [模型说明](docs/model.md)
- [数据获取、AI 配置和部署](docs/data.md)
- [演示和验收](docs/demo.md)
- [上游代码与许可证](docs/upstream.md)

## 环境切换

| 模式 | 适用范围 | 所需配置 |
| --- | --- | --- |
| `fixture` | 离线演示 | 本地演示账号或自建账号 |
| `openbb` | 内部联网验证 | 安装 `openbb` extra、显式启用研究行情、配置估值日利率和股息 |
| `longbridge` | 港美股实时行情 | 长桥 OpenAPI 凭证、港股实时权限、利率假设和独立账号 |
| `vendor` | 公司授权行情接入 | HTTPS 数据接口、服务端凭证、当期定价假设和独立账号 |

容器演示可执行 `docker compose up --build`。端口绑定在本机回环地址。Render 可使用仓库的 `Dockerfile`，健康检查路径为 `/healthz`。启用实时行情和 AI 的 [Render 配置步骤](docs/data.md#render-开启行情和-ai) 见数据文档。
