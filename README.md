# TronEnergy

基于 TRON Stake 2.0 的 Energy / Bandwidth 租赁后端，Python + PostgreSQL + Redis。

第一版已实现核心 API、报价与订单、事务预占、持久任务、链委托/回收适配、隔离签名、费用估算和日志审计。默认关闭签名和广播，当前为测试网验收版本，主网执行尚未开放。

- [运行、API 和实现边界](docs/implementation-v1.md)
- [设计文档](docs/design-v1.md)
- [服务说明](services/README.md)

## 安装

```bash
python -m venv .venv
# 激活虚拟环境后
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
```

复制 .env.example 为 .env 并设置凭据，按运行说明准备数据库、Redis 和测试网配置。

```bash
python -m tronenergy_storage.migrate
python -m pytest -q
```

也可使用 deploy/compose/compose.yaml 启动开发环境；签名禁用时配置空的 config/local/signer.key 即可。不要提交私钥。

## 目录

```text
services/       api、rental、chain、signer、observer
packages/       domain、storage、observability
migrations/     PostgreSQL 版本化 SQL 迁移
config/         环境配置位置
tests/          单元、数据库集成、测试网验证位置
deploy/         统一容器镜像与 Compose
docs/           设计和运行文档
```
