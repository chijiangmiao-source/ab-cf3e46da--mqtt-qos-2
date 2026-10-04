# 星载遥测中继 · MQTT 5 QoS2 重放审计

审查断链后重发的 MQTT 5 QoS 2 报文：判定同一遥测是否**恰好交付一次**，
识别因丢失确认造成的**未闭合会话**，并按包序号稳定定位违规依据。

## 能力

- 录入：稳定审计标识、客户端标识，以及最多 **48 条**按捕获顺序排列、
  带方向（C2S / S2C）的 MQTT 5 控制包。
- 受理控制包：`CONNECT / CONNACK / PUBLISH(QoS2) / PUBREC / PUBREL /
  PUBCOMP / DISCONNECT`；其余或字段串扰直接判违规。
- 同一持久会话内按 **Clean Start / Session Present / DUP / 包标识** 校验
  重连恢复、合法重放与未闭合交换。
- 逐包证据：会话阶段、包标识、首次交付状态、首个违规依据（稳定代码+序号）。
- 裁决结论：`ACCEPTED`（全部恰好交付一次）/ `OPEN`（存在未闭合交换）/
  `REJECTED`（首个结构违规，含闭合后重复交付、载荷冲突、阶段跳跃、
  方向错误、清会话后沿用旧确认等）。
- 审计标识规则：
  - 相同标识 + **完全相同输入** → 回放原裁决（HTTP 200，`replayed=true`）；
  - 相同标识 + **改动输入** → 拒绝（HTTP 409），原证据保留并可重开；
  - 按标识 `GET /api/audits/{id}` 重新打开历史裁决。

## 目录结构

```
backend/app/engine.py    裁决引擎（纯函数、确定性）
backend/app/storage.py   指纹去重与证据持久化（/data/audits.json）
backend/app/main.py      FastAPI：健康检查、提交/回放/重开、静态页面
backend/tests/           30 项引擎与 API 测试
frontend/                Vue 3 + Vite 联调页面（逐包证据表+场景模板）
scripts/smoke.py         HTTP 冒烟（合法重发仅一次 / 冲突重发被拒 / 回放保留）
Dockerfile               frontend 构建 -> runtime -> verify 三阶段
docker-compose.yaml      web（HOST_PORT 可配置）+ verify
```

## Docker 启动

```bash
docker compose up -d --build
# 浏览器打开 http://localhost:8080 （宿主机端口可改）
HOST_PORT=9090 docker compose up -d
```

健康检查：`GET /api/health` → `{"status":"ok"}`。

## verify（代码测试 + 页面构建 + API/HTTP 冒烟）

```bash
docker compose build web verify
docker compose up -d web
docker compose run --rm verify      # 退出码 0 通过，非 0 失败
```

无 Docker 时：`./verify.sh`（自动建虚拟环境、构建、起服务并冒烟）。

冒烟覆盖：断链后 Clean Start=0 / SP=1 重连，DUP PUBLISH 重发后完整闭合，
裁决 `ACCEPTED` 且 `delivered_once=1`；PUBCOMP 闭合后再发 DUP PUBLISH，
裁决 `REJECTED`、`duplicate_delivery=true` 并定位到具体包序号；
改输入复用审计标识返回 409 且原证据不变。

## 裁决规则要点

| 场景 | 判定 |
|---|---|
| PUBLISH→PUBREC→PUBREL→PUBCOMP 完整 | ACCEPTED，交付一次 |
| 断链（S2C DISCONNECT）后重连，未闭合交换 DUP=1、载荷一致重发并闭合 | ACCEPTED，仍只计一次 |
| PUBCOMP 丢失，重连后重发 PUBREL（无 DUP）并再闭合 | ACCEPTED |
| 缺 PUBREC 直接 PUBREL / 缺 PUBREL 直接 PUBCOMP | 阶段跳跃，定位违规包 |
| PUBREC/PUBCOMP 出现在 C2S、PUBREL 出现在 S2C | 方向错误 |
| 重发主题/载荷与首次不一致 | 载荷冲突 `PACKET_ID_REUSED_BY_DIFFERENT_PUBLICATION` |
| 闭合后再 DUP PUBLISH | 重复交付 `DUP_PUBLISH_AFTER_QOS2_COMPLETE` |
| PUBREL 已发后又重发 PUBLISH | 阶段倒退 `DUP_PUBLISH_AFTER_PUBREL` |
| Clean Start=1 后继续旧 PUBREC/PUBREL/PUBCOMP | 未知包标识违规 |
| SP=1 但无可恢复会话；CS=0 有未闭合状态却 SP=0 | CONNACK 会话标志冲突 |
| 序列结束仍有交换未到 PUBCOMP | OPEN，列出未闭合包标识与阶段 |
