# 部署

containers/Dockerfile 构建统一镜像，compose/compose.yaml 提供 PostgreSQL、Redis、迁移任务和六个应用进程。仅 signer 挂载私钥文件，对外仅开放回环地址 8000。

在仓库根目录准备 .env 与 config/local/signer.key，然后执行：

```bash
docker compose --env-file .env -f deploy/compose/compose.yaml up --build -d
```

签名禁用时私钥文件可以为空。其余目录预留给后续生产编排、主备、备份与可观测性配置。当前不提供主网高可用部署。详见 [运行说明](../docs/implementation-v1.md)。

## GitHub Actions

- `CI`：推送到 main/master/debug、提交 PR 或手动运行时触发。使用 Python 3.13 安装锁定依赖和项目，执行 Ruff 并构建统一 Docker 镜像。独立 CI 的 push 或手动运行还会推送镜像；PR 和 CD 调用 CI 时不推送。
- `CD`：在 Actions → CD → Run workflow 选择分支/标签手动运行，或推送 `v*` 标签触发。先调用完整 CI，通过后发布统一镜像到 `ghcr.io/<owner>/<repo>`（全小写）。当前完成镜像交付，不自动连接服务器、执行生产迁移或重启服务。

CI 使用 `.github/workflows/ci.yml` 顶层的 `REGISTRY` 和 `BACKEND_IMAGE`，与 hexsight 配置方式一致。默认发布到 `crpi-9jwlonaugv9hai08.cn-guangzhou.personal.cr.aliyuncs.com/snake-2001/tronenergy:latest`。在 tronenergy 仓库 Settings → Secrets and variables → Actions 中配置 `REGISTRY_USERNAME` 和 `REGISTRY_PASSWORD`，凭据需有目标镜像仓库的推送权限。`REGISTRY` 包含命名空间，登录时自动提取域名；将其留空可禁用推送。main/master/debug 的推送和手动运行共用这个镜像标签，后完成的推送会覆盖它。

CD 发布的 GHCR 镜像包含 `sha-<完整提交 SHA>` 标签；版本标签触发时另生成对应标签，例如 `v0.1.0`。CD 不更新 `latest`，便于明确选择部署版本。每次 CD 发布的镜像 digest 可在 Actions 运行摘要查看。

GHCR 登录使用 GitHub 自动提供的 `GITHUB_TOKEN`，无需新增个人令牌。仓库/组织需允许 Actions 发布 Packages；若同名包已经存在，需在包设置中授予当前仓库写入权限。发布方式参考 [GitHub 官方说明](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)。

也可以用已登录的 GitHub CLI 操作（将 `<owner>/<repo>` 替换成仓库名）：

```bash
gh workflow run ci.yml --repo <owner>/<repo> --ref main
gh workflow run cd.yml --repo <owner>/<repo> --ref main
gh run list --repo <owner>/<repo>
gh run watch <run-id> --repo <owner>/<repo>
```

首次使用前需将工作流文件推送到默认分支，才能从界面或 CLI 手动触发。现有 Compose 仍从源码构建；服务器使用发布镜像时，应将应用服务的镜像配置为摘要中给出的 `ghcr.io/<owner>/<repo>@sha256:...` 并使用 `--no-build`，同时保留服务器自己的环境配置和私钥挂载。
