# 服务

| 目录 | 进程名 | 入口 |
|---|---|---|
| api | api-service | tronenergy_api.main:app |
| rental | rental-service | tronenergy_rental.main:app |
| rental | rental-worker | python -m tronenergy_rental.worker |
| chain | chain-service | tronenergy_chain.main:app |
| signer | signer-service | tronenergy_signer.main:app |
| observer | observer-worker | python -m tronenergy_observer.worker |

rental 的两个进程属于同一逻辑服务。仅 API 提供客户入口；内部服务需要独立凭据，只有 signer 持有私钥文件。详见 [运行说明](../docs/implementation-v1.md)。
