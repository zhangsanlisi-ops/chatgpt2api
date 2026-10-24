# Sentinel 私有 Chromium solver

## 部署

`docker compose up -d --build` 同时启动主服务和 `sentinel_solver`。在 `.env` 配置随机的 `SENTINEL_SOLVER_KEY`（至少 32 字节）、`SENTINEL_SOLVER_TIMEZONE`；主服务通过 Docker 内部地址 `http://sentinel_solver:8080` 访问 solver。solver 只声明 `expose: 8080`，**不要**配置宿主机 `ports` 或公网反向代理。`/health` 也要求 Bearer 密钥。

默认 solver 使用匿名常驻 Chromium，从当前 `chatgpt.com` 页面取得 Sentinel SDK，在同一页面执行 `p`、PoW 和 Turnstile。主服务从 solver 返回的同一浏览器指纹设置 UA、Client Hints 和时区。当前 `p` 是 25 字段、`~S` 后缀；PoW 使用当前 SDK 的 FNV-1a 32-bit + avalanche，不能退回 SHA3。`finalize` 同时提交 `prepare_token`、`proofofwork` 和 `turnstile`。

主服务发送给 solver 的 JSON 限定为：空的 requirements 请求，或 `generation`、PoW `required/seed/difficulty`、Turnstile `required/dx`。不发送账号 access token、Cookie、`prepare_token`、用户 prompt 或图片。API 模型禁止额外字段。密钥只放环境变量，不写日志和文档。

## 出口一致性

部署时先确认主服务到上游的**实际出口**地理位置/时区，再设置 `SENTINEL_SOLVER_TIMEZONE`。若主服务走代理，`SENTINEL_SOLVER_PROXY` 应指向相同出口；不能用主服务代理出口与 solver 直连出口混搭。当前部署的直连出口为日本东京，配置 `Asia/Tokyo`；这是该部署的观测值，不是通用默认值。若账号级代理有多个不同出口，单个 solver/时区不能保证一致，需要按出口分组部署或增加路由，不能仅复用当前单实例。

## 验收

1. `docker compose ps`：主服务和 solver 都运行，solver 为 healthy，solver 无宿主机端口映射。
2. 测试 requirements：`p` 解码为 25 字段，第 5 字段与返回 UA 一致，时区与实际出口相符。
3. 用真实图片会话验证 prepare、PoW/Turnstile、finalize；日志不得出现密钥、token 或 prompt。
4. 分别调用文生图和图生图。**只有实际返回图片、完成下载并完整解码后才算成功**；握手成功、HTTP 200 或 SSE 事件均不能替代图片验收。
5. 若实际 `auto` 图片会话返回精确 404 `Model not found`，核对原重试预算内换账号、失败账号冷却约 600 秒，并确认额度、账号状态及代理健康分未被改写。其它 404 不应进入该分支。

## 回滚

停止 compose 后恢复原 `.env` 镜像配置及原部署文件，再启动旧版本。保留 `data/` 和 `config.json`；不要删除账号数据。冷却记录仅在内存中，重启自动消失。旧版 SHA3/模拟 Turnstile 不是可用的当前握手实现，回滚仅用于恢复既有部署状态，不代表能恢复生图。
