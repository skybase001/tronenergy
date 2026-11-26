# 数据库迁移

在仓库根目录执行 python -m tronenergy_storage.migrate。

迁移器按名称执行数字编号 SQL，在事务中持有迁移锁并记录 SHA-256 校验和。已执行的迁移不能原地修改；后续变更新增编号文件。

rental/versions 与 chain/versions 保留给后续按所有权拆分迁移。第一版使用统一 SQL 基线，不并行运行 Alembic。生产迁移账号与应用账号应分离。
