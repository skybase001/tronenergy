# 第一版实现与运行说明

本版本提供可运行的后端核心流程，定位为测试网验收版本；尚未进行真实测试网广播或生产验收，不能视为已经验收的主网平台。默认关闭签名和广播，配置校验拒绝主网执行。

## 已实现

- API 网关、租赁服务/Worker、链执行、隔离签名、观察与对账 Worker。
- Energy / Bandwidth 报价、订单、人工结算批准、资源预占、委托与到期回收。
- PostgreSQL 原子事务、请求幂等、追加审计、持久 Outbox、任务租约版本。
- TRX / 白名单 TRC-20 费用估算、账户激活费用、合约模拟及降级。
- 固化确认、委托关系核验、广播超时重播相同交易、未知交易隔离。
- 管理员添加/暂停/恢复池、重试任务、登记人工退款、查看审计与指标。
- 租户 API Key、管理员权限、Redis 公网限流、结构化 INFO / DEBUG 日志。
- 数据库迁移、Docker Compose、单元测试和真实 PostgreSQL 集成测试。

## 明确的实施边界

1. 使用 psycopg 参数化 SQL 和显式事务，暂不引入 SQLAlchemy；迁移使用编号 SQL、校验和与数据库锁，暂不引入 Alembic。
2. rental-service 和 rental-worker 是同一逻辑服务的 HTTP 与后台进程。
3. 每个订单动作保留一笔 chain_operations 记录，包含原文、签名、txid。未实现安全重建新交易：过期/不确定状态进入 RECONCILING；重试按钮不删除原 txid，也不跳过核验。后续需实现固化区块完整扫描与尝试归档。
4. signer 从只读文件加载单个测试网 owner 私钥，只支持对应池的两种资源操作。尚未实现 HSM、多签、active permission、多密钥路由。其他地址不能使用此 signer 签名。
5. 当前使用单个可配置 RPC，写入前检查已配置的创世区块 ID；尚无第二 RPC 仲裁和自动故障切换。
6. Compose 使用开发数据库账号；生产最小权限、主备、备份、不可篡改审计归档仍需独立部署。
7. 提供管理指标 JSON 与日志；Prometheus、告警、追踪后端、日志定期归档尚未部署。
8. Redis 限流失败时公网请求返回 503，后台回收不依赖 Redis；当前链参数与资源查询实时读取，未引入短期缓存。
9. 只支持取消 PENDING_APPROVAL；已批准订单可能已开始构建交易，不开放取消。
10. 报价 TTL 默认 30 秒，包含批准等待时间。较长人工流程需调整 TTL；过期后重新接受新报价并创建订单。

这些边界与 design-v1.md 的完整目标存在差距，不能把第一版测试通过等同于生产设计全部完成。

## 安装与配置

要求 Python 3.12+，本次使用 Python 3.13。在虚拟环境中执行：

```bash
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
```

复制 .env.example 为 .env，替换所有凭据。TE_API_KEYS 是 API Key 到 tenant_id 的 JSON 映射；管理员、内部服务、签名服务分别使用独立凭据。

## Docker Compose

要求 Docker Engine + Compose 插件。先创建 config/local/signer.key；禁用签名时可以是空文件，启用时只使用测试网私钥。该文件已被 Git 忽略。

在仓库根目录执行：

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml up --build -d
docker compose --env-file .env -f deploy/compose/compose.yaml logs -f
```

迁移成功后启动服务，只公开 127.0.0.1:8000，只有 signer 挂载私钥文件。Compose 为单机开发配置；不提供生产主备或写实例自动迁移。

## 独立进程启动

准备 PostgreSQL 与 Redis，设置连接地址，然后执行迁移：

```bash
python -m tronenergy_storage.migrate
```

在独立终端分别运行：

| 进程 | 命令 |
|---|---|
| api-service | uvicorn tronenergy_api.main:app --host 127.0.0.1 --port 8000 |
| rental-service | uvicorn tronenergy_rental.main:app --host 127.0.0.1 --port 8001 |
| rental-worker | python -m tronenergy_rental.worker |
| chain-service | uvicorn tronenergy_chain.main:app --host 127.0.0.1 --port 8002 |
| signer-service | uvicorn tronenergy_signer.main:app --host 127.0.0.1 --port 8003 |
| observer-worker | python -m tronenergy_observer.worker |

外部部署需要 HTTPS 反向代理、请求体上限、内部网络隔离。不要对公网开放内部服务或签名服务端口。

## API 流程

请求携带 X-API-Key；业务 POST 携带 Idempotency-Key。相同操作重试复用原键，不能改变请求体。

1. 管理员 POST /v1/admin/pools，提交 address，登记已激活且已完成 Stake 2.0 质押的池。
2. 客户 POST /v1/quotes，提交 resource_type、target_units、duration_seconds。
3. 客户 POST /v1/orders，提交 quote_id、receiver_address。
4. 管理员 POST /v1/admin/orders/{id}/approve，提交 reason、settlement_reference。
5. 客户 GET /v1/orders/{id} 或 GET /v1/orders，查看状态和交易。
6. 固化确认后 ACTIVE，从区块时间计算租期；到期撤销委托并确认后 COMPLETED。
7. 失败订单若需退款，由管理员线下退款，然后 POST /v1/admin/orders/{id}/refund 登记 reason 与 settlement_reference。接口本身不转账。

报价请求示例：

```json
{"resource_type":"ENERGY","target_units":65000,"duration_seconds":3600}
```

转账估算 POST /v1/estimates/transfers：

```json
{"kind":"TRX","sender":"<发送地址>","receiver":"<接收地址>","amount":1000000,"signatures":1}
```

TRC-20 设置 kind=TRC20 和 contract_address，amount 使用代币最小单位。合约必须列入 TE_TOKEN_CONTRACTS。估算不签名、不广播客户转账。

管理入口：

- GET /v1/admin/orders/{id}：跨租户管理员查询。
- GET /v1/admin/pools：池及最近容量快照。
- POST /v1/admin/pools/{id}/pause 或 resume：提交 reason；暂停仅限制新委托，不停止回收。
- POST /v1/admin/orders/{id}/retry：提交 reason，重试 BLOCKED 任务，不重建未知交易。
- GET /v1/admin/audit?target={order_id}：订单审计；链操作审计 target 为 {order_id}:DELEGATE 或 :RECLAIM。
- GET /v1/admin/metrics：订单、任务、逾期订单、最老任务等待时间。
- GET /health/live、/health/ready：存活与数据库连通性，不表示链写入可用。

具体接口类型可在本机 rental-service 的 /docs 查看。网关采用统一代理路由，其 /docs 不列出全部业务模型。

## 启用测试网写入

确认读路径可用后，配置 nile 或 shasta RPC；从可信来源核对创世区块 ID 并设置 TE_GENESIS_BLOCK_ID。配置对应资源池的测试私钥文件，设置 TE_SIGNING_ENABLED=true 与 TE_BROADCASTING_ENABLED=true 后重启相关服务。

先用小额资源分别验证两种资源的交付、固化和回收。不要使用主网私钥；当前主网写入会被拒绝。

## 故障处理

- 广播超时：查询原 txid，保留签名原文，不删除交易后重发新交易。
- RECONCILING：查询固化记录和委托关系，观察器继续追踪晚到确认；当前不会自动证明交易不存在并重建。
- BLOCKED：查看错误、依赖与审计，修复后调用 retry；不能将链上失败状态人为改成成功。
- 写实例迁移：隔离旧进程签名和 RPC 出口，核对未决交易后再启动新实例。
- 数据库恢复：关闭签名/广播，恢复备份并核对未完成订单，再恢复写入。
- 审计表只追加写入；数据库管理员仍可修改，生产必须限制角色与归档。
- chain_operations 含签名交易恢复数据，必须限制读取权限，不能输出到日志。

## 测试

```bash
python -m pytest -q
ruff check services packages tests
ruff format --check services packages tests
```

设置 TE_TEST_DATABASE_URL 为隔离 PostgreSQL 测试连接后可运行集成测试。每个测试创建随机 schema 并在结束时删除该 schema，不清空应用 schema。测试账号需有创建 schema 权限。

测试链交互使用可控替身，没有真实广播。真实测试网验收需要资源池、接收地址、RPC 和测试密钥，本次不会自动创建账户或动用资金。
