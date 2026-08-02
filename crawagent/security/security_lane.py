"""安全 Lane 编排（P5-1）

SecurityLane 封装完整的扫描流程：
1. 创建扫描任务（ScanTask）
2. 运行 VulnScanner 扫描
3. 漏洞持久化（SecurityStore）
4. 生成修复补丁（AutoFixer）
5. 返回 ScanResult + 补丁列表

与 main lane 并行运行，互不干扰。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from crawagent.security.models import (
    ScanTask, ScanResult, Vulnerability, Severity, VulnCategory,
    SecurityStore, get_security_store, ScanStatus,
)
from crawagent.security.vuln_scanner import VulnScanner
from crawagent.security.auto_fixer import AutoFixer, FixPatch


class SecurityLane:
    """安全扫描 Lane

    用法：
        lane = SecurityLane()
        result = await lane.run_scan(
            url="https://example.com",
            categories=[VulnCategory.A03_INJECTION, VulnCategory.XSS],
        )
        # result.scan_result / result.patches
    """

    def __init__(self, store: Optional[SecurityStore] = None) -> None:
        self.store = store or get_security_store()
        self.scanner = VulnScanner
        self.fixer = AutoFixer()

    async def run_scan(
        self,
        url: str,
        name: str = "",
        categories: Optional[List[VulnCategory]] = None,
        depth: int = 1,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        max_requests: int = 100,
        generate_patches: bool = True,
    ) -> Dict[str, Any]:
        """执行完整安全扫描流程

        Returns:
            {
                "task": ScanTask,
                "scan_result": ScanResult,
                "patches": List[FixPatch],
                "patch_files": List[str],  # 保存的补丁文件路径（如生成）
            }
        """
        # 1. 创建扫描任务
        task = ScanTask(
            name=name or f"扫描 {url}",
            url=url,
            categories=categories or [],
            depth=depth,
            headers=headers or {},
            timeout=timeout,
        )
        self.store.create_task(task)
        logger.info(f"[SecurityLane] 启动扫描任务 {task.id} url={url}")

        # 2. 运行扫描器
        scanner = self.scanner(task=task, max_requests=max_requests)
        scan_result = await scanner.scan()

        # 3. 漏洞持久化
        for vuln in scan_result.vulnerabilities:
            self.store.add_vulnerability(vuln)
        self.store.update_task(task)

        logger.info(
            f"[SecurityLane] 扫描完成: {len(scan_result.vulnerabilities)} 个漏洞, "
            f"{scan_result.requests_sent} 次请求, "
            f"耗时 {scan_result.duration_seconds:.1f}s"
        )

        # 4. 生成修复补丁（默认只自动处理低/中危；高危/严重仅报告，见 AutoFixer 白名单）
        patches: List[FixPatch] = []
        patch_files: List[str] = []
        reported_only: List[Vulnerability] = []
        if generate_patches and scan_result.vulnerabilities:
            patches, reported_only = await self.fixer.generate_patches(
                scan_result.vulnerabilities, scan_task_id=task.id
            )

        # 5. 按等级分组报告（安全页按严重性分级展示）
        report_by_severity: Dict[str, List[Dict[str, Any]]] = {}
        for sev in ("critical", "high", "medium", "low", "info"):
            group = [
                v.model_dump()
                for v in scan_result.vulnerabilities
                if v.severity.value == sev
            ]
            if group:
                report_by_severity[sev] = group

        return {
            "task": task,
            "scan_result": scan_result,
            "patches": patches,
            "patch_files": patch_files,
            "reported_only": [v.model_dump() for v in reported_only],
            "report_by_severity": report_by_severity,
        }

    async def run_task_once(self, task_id: str) -> Dict[str, Any]:
        """重新运行已存在的扫描任务"""
        task = self.store.get_task(task_id)
        if not task:
            return {"error": f"扫描任务不存在: {task_id}"}

        return await self.run_scan(
            url=task.url,
            name=task.name,
            categories=task.categories,
            depth=task.depth,
            headers=task.headers,
            timeout=task.timeout,
        )

    async def save_patches(
        self, patches: List[FixPatch], output_dir: str = "./output/security_patches"
    ) -> List[str]:
        """保存补丁到文件"""
        return await self.fixer.save_patches(patches, output_dir)
