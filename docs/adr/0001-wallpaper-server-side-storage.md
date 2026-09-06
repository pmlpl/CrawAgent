# 0001 — 聊天背景图存储在服务端（data/），不用 localStorage

浏览器 localStorage 按 origin（协议+主机+端口）隔离：`localhost:8006` 与 `127.0.0.1:8006` 是两个 origin，互相看不到对方的背景设置；换浏览器同样不共享；且 ~5MB 配额曾顶爆（docs/07 历史 Bug，2026-09-06）。决定：背景图上传后存到服务端 `data/background.jpg`（遮罩浓度存 `data/background.json`），前端启动时从 `/api/background` 拉取——任何 origin、任何浏览器看到同一份。

## Considered Options

- 后端把 127.0.0.1 重定向到 localhost：治标，跨浏览器/配额问题仍在
- 仅文档约定固定用一个地址访问：零成本，坑全留着

## Consequences

- 背景数据生命周期跟随 `data/` 目录（清空 data/ 即重置外观）
- 前端不再有「压缩后仍超出存储上限」一类报错；保存失败均为服务端磁盘/请求错误，显式提示
