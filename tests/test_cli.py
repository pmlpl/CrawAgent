"""CLI status 命令测试（JobStore 查询）。"""

from click.testing import CliRunner

from crawagent.cli.main import cli


class _FakeStore:
    def __init__(self, job):
        self._job = job

    async def get_job(self, job_id):
        return self._job


def test_cli_status_job_not_found(monkeypatch):
    monkeypatch.setattr("crawagent.core.job_store.get_job_store", lambda: _FakeStore(None))
    result = CliRunner().invoke(cli, ["status", "job-404"])
    assert result.exit_code == 0
    assert "不存在" in result.output


def test_cli_status_job_found(monkeypatch):
    job = {
        "job_id": "job-1",
        "instruction": "爬取示例站点",
        "seed_urls": ["https://example.com"],
        "status": "running",
        "progress": {"pages": 3, "done": 2},
        "items_count": 2,
        "items": [],
        "logs": [],
        "error": None,
        "created_at": 1750000000.0,
        "updated_at": 1750000100.0,
    }
    monkeypatch.setattr("crawagent.core.job_store.get_job_store", lambda: _FakeStore(job))
    result = CliRunner().invoke(cli, ["status", "job-1"])
    assert result.exit_code == 0
    assert "爬取示例站点" in result.output
    assert "running" in result.output
    assert "2" in result.output
    assert "https://example.com" in result.output
