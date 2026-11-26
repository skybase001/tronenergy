# 公共模块

- domain：领域输入、资源计算、配置与 HTTP 公共支持。
- storage：psycopg 连接池、事务、审计写入与 SQL 迁移。
- observability：JSON 结构化日志。

根目录 pyproject.toml 将各 src 包映射为一个 Python 发行包。models.py 的领域计算不执行 I/O。当前实现采用平铺小模块；预留子目录供后续按规模拆分。
