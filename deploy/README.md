# 部署

containers/Dockerfile 构建统一镜像，compose/compose.yaml 提供 PostgreSQL、Redis、迁移任务和六个应用进程。仅 signer 挂载私钥文件，对外仅开放回环地址 8000。

在仓库根目录准备 .env 与 config/local/signer.key，然后执行：

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml up --build -d
```

签名禁用时私钥文件可以为空。其余目录预留给后续生产编排、主备、备份与可观测性配置。当前不提供主网高可用部署。详见 [运行说明](../docs/implementation-v1.md)。
