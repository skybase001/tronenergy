# 测试

执行 python -m pytest -q。未设置 TE_TEST_DATABASE_URL 时 PostgreSQL 集成测试跳过。

- unit：计算、配置、序列化签名策略、鉴权、估算。
- integration：真实 PostgreSQL 上的回滚、并发、幂等、任务租约、回收、广播超时重播。
- testnet：保留给真实网络验收，当前不会自动广播。
- fixtures：预留脱敏样例。

集成测试使用随机 schema，结束时仅删除该 schema。链交互使用可控替身，不代表真实测试网验收。
