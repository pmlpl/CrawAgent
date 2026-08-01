"""告警通知（P4-4）：Webhook / 飞书 / 邮件

支持：
- Webhook：POST JSON 到指定 URL
- 飞书：通过 Webhook 发送卡片消息
- 邮件：SMTP 发送（可选）

设计原则：
- 告警去抖由 MonitorStore 的 last_alert_at + task.alert_cooldown 控制
- 通知失败不阻塞监控流程，只记录 notify_error
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

import httpx
from loguru import logger

from crawagent.monitor.models import AlertRecord, MonitorTask


class Notifier:
    """告警通知器

    用法：
        notifier = Notifier()
        await notifier.notify(task, alert)
    """

    async def notify(
        self,
        task: MonitorTask,
        alert: AlertRecord,
    ) -> Dict[str, Any]:
        """发送告警通知

        Args:
            task: 监控任务（含 webhook 配置）
            alert: 告警记录

        Returns:
            {"success": bool, "error": str}
        """
        if not task.alert_webhook:
            logger.debug(f"[Notifier] 任务 {task.id} 未配置 webhook，跳过通知")
            return {"success": False, "error": "no webhook configured"}

        webhook_url = task.alert_webhook
        # 飞书 webhook 识别
        is_feishu = "feishu" in webhook_url or "larksuite" in webhook_url

        try:
            if is_feishu:
                payload = self._build_feishu_payload(task, alert)
            else:
                payload = self._build_webhook_payload(task, alert)

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                trust_env=False,
            ) as client:
                resp = await client.post(webhook_url, json=payload)
                if resp.status_code < 400:
                    logger.info(
                        f"[Notifier] 告警已发送 task={task.id} level={alert.level} "
                        f"webhook={webhook_url[:50]}..."
                    )
                    return {"success": True, "error": ""}
                return {
                    "success": False,
                    "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                }
        except Exception as e:
            logger.warning(f"[Notifier] 通知失败: {e}")
            return {"success": False, "error": str(e)}

    def _build_webhook_payload(
        self, task: MonitorTask, alert: AlertRecord
    ) -> Dict[str, Any]:
        """通用 Webhook payload"""
        return {
            "event": "monitor_alert",
            "task_id": task.id,
            "task_name": task.name,
            "level": alert.level.value,
            "title": alert.title,
            "message": alert.message,
            "url": alert.url or task.url,
            "diff_summary": alert.diff_summary,
            "old_value": alert.old_value,
            "new_value": alert.new_value,
            "timestamp": alert.created_at,
            "iso_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(alert.created_at)),
        }

    def _build_feishu_payload(
        self, task: MonitorTask, alert: AlertRecord
    ) -> Dict[str, Any]:
        """飞书 Webhook 卡片消息"""
        level_emoji = {
            "info": "ℹ️",
            "warn": "⚠️",
            "error": "🔴",
            "critical": "🚨",
        }
        emoji = level_emoji.get(alert.level.value, "📢")
        content_lines = [
            f"{emoji} **{alert.title}**",
            f"",
            f"**任务**: {task.name}",
            f"**URL**: {alert.url}",
            f"**变化**: {alert.diff_summary}",
            f"**时间**: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(alert.created_at))}",
        ]
        if alert.old_value or alert.new_value:
            content_lines.append(f"**旧值**: {alert.old_value}")
            content_lines.append(f"**新值**: {alert.new_value}")
        content = "\n".join(content_lines)
        return {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"{emoji} CrawAgent 监控告警"},
                    "template": "red" if alert.level.value in ("error", "critical") else "yellow",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                ],
            },
        }

    async def send_test(self, webhook_url: str) -> Dict[str, Any]:
        """发送测试通知（验证 webhook 是否可用）"""
        test_alert = AlertRecord(
            task_id="test",
            level="info",
            title="测试通知",
            message="这是一条来自 CrawAgent 的测试通知",
            url="",
            diff_summary="测试",
        )
        test_task = MonitorTask(
            name="测试任务",
            url="",
            alert_webhook=webhook_url,
        )
        return await self.notify(test_task, test_alert)
