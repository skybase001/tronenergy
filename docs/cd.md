# GitHub Actions CD

与 HexSight 一致：CI 构建并推送阿里云镜像，然后通过 workflow_call 调用 CD，经堡垒机 ProxyJump SSH 到部署服务器。CD 不再发布 GHCR 镜像。

CI 当前 main、master、debug 的 push 和手动运行均部署到同一个目标，与现有 CI 触发范围一致。PR 只构建不部署。生产与调试需隔离时，应先拆分分支条件及部署环境。

## Actions 配置

Variables：

- DEPLOY_HOST、DEPLOY_USER、DEPLOY_PORT（默认 22）。
- BASTION_HOST、BASTION_USER、BASTION_PORT（默认 22）。
- DEPLOY_COMPOSE_FILE：服务器上 Compose 文件的绝对路径。
- DEPLOY_ENV_FILE：服务器上环境文件绝对路径；未设置时使用 Compose 文件同目录的 .env。按仓库布局部署时，应明确设为仓库根目录的 .env。

Secrets：

- REGISTRY_USERNAME、REGISTRY_PASSWORD。
- DEPLOY_SSH_PRIVATE_KEY、DEPLOY_SSH_KNOWN_HOSTS。
- BASTION_SSH_PRIVATE_KEY、BASTION_SSH_KNOWN_HOSTS。

known_hosts 必须包含经过核对的主机密钥；非默认端口使用对应的 [host]:port 记录。部署账号需要 Docker 权限，服务器必须有支持 --wait 的 Docker Compose v2。

## 服务器准备

将更新后的 deploy/compose/compose.yaml 放到服务器，保留相对目录布局；其 env_file 和 signer secret 的相对路径仍然有效。准备仓库根目录 .env 和 config/local/signer.key。首次部署前确保已配置数据库密码和服务凭据。

CD 不上传 Compose、环境文件或私钥，也不修改签名与广播开关。迁移代码和应用代码均来自同一个 sha-提交号 镜像。TE_IMAGE 由 CD 导出，覆盖环境文件中的旧镜像配置。

## 执行顺序

1. 校验配置与 SSH 主机密钥，经堡垒机连接目标服务器。
2. 临时登录阿里云仓库并拉取本次提交镜像。
3. 启动/检查 PostgreSQL 和 Redis。
4. 停止六个旧应用进程，等待最多 60 秒。
5. 使用新镜像运行一次数据库迁移；失败立即终止，应用保持停止。
6. 使用新镜像启动六个应用进程，不本地构建，不重复启动迁移容器。
7. 检查容器运行状态和 API 数据库就绪接口。

部署存在短暂停机；不删除数据库卷，不执行自动回滚。迁移失败时先检查日志和数据库状态，不能直接启动旧版本假定兼容。Worker 的运行状态检查不代表链上履约成功，仍需监控任务积压和对账。

仓库临时登录配置及 SSH 私钥在退出时删除；凭据通过 SSH 加密 stdin 传输，不进入远程命令参数。工作流只串行化本仓库部署，外部手工部署仍需避免并发。
