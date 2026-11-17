# FCN 估值工作台

港美股单标的固定票息票据估值工具。首版支持三个月、无敲入、无敲出或每月敲出。客户先确认参考收盘价、接货价和观察日期，再计算估算年化票息、持有至到期的票息比例和结算情景。结果与固定行情快照关联，可查看历史记录或导出 PDF。

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
| `vendor` | 公司授权行情接入 | HTTPS 数据接口、服务端凭证和对应估值日的定价假设 |

容器演示可执行 `docker compose up --build`。端口绑定在本机回环地址。面向客户部署时使用授权行情、实名账号、独立密钥和公司 HTTPS 网关，具体环境变量与接口格式见数据文档。
