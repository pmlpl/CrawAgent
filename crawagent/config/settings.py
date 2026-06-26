"""配置管理 —— 加载 models.json

文件结构（非常简单）:
    {
      "current": "deepseek",
      "models": [
        {
          "name": "deepseek",
          "provider": "openai",
          "base_url": "https://api.deepseek.com/v1",
          "model_name": "deepseek-chat",
          "api_key": "sk-xxxxxxxxxx",
          "temperature": 0.7,
          "max_tokens": 2000
        },
        {
          "name": "ollama-qwen",
          "provider": "ollama",
          "base_url": "http://localhost:11434",
          "model_name": "qwen2.5:7b",
          "api_key": "",
          "temperature": 0.7,
          "max_tokens": 2000
        }
      ]
    }

provider 只有两种:
  - "openai"     —— 任何 OpenAI 兼容 API（DeepSeek / OpenAI / LM Studio / Ollama / 通义千问...）
  - "anthropic"  —— Anthropic Claude 系列
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


# ---------- 日志配置 ----------
def setup_logging(level: int = logging.INFO) -> None:
    """配置全局日志系统"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的 logger"""
    return logging.getLogger(name)


logger = get_logger(__name__)


@dataclass
class ModelConfig:
    name: str
    provider: str           # "openai" 或 "anthropic"
    base_url: str
    model_name: str
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 2000
    description: str = ""

    def display(self) -> str:
        if self.description:
            return f"{self.name} [{self.provider}] {self.description}"
        return f"{self.name} [{self.provider}] {self.model_name}"


class Settings:
    """全局配置 —— 从 models.json 加载"""

    def __init__(self, models_file: Path):
        self.models_file = models_file
        self.models: list[ModelConfig] = []
        self.current: str = ""
        self.project_root = models_file.parent
        self.output_dir = self.project_root / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._load()

    # ---------- 加载 / 保存 ----------

    def _load(self) -> None:
        if not self.models_file.exists():
            self._create_default()
            return
        try:
            with open(self.models_file, "r", encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
            self.models = [self._load_model_config(m) for m in data.get("models", [])]
            self.current = str(data.get("current", "")) or (self.models[0].name if self.models else "")
        except Exception as e:
            print(f"[警告] 加载 {self.models_file.name} 失败: {e}")
            self._create_default()
    
    def _load_model_config(self, model_data: dict) -> ModelConfig:
        """加载单个模型配置，支持环境变量读取 api_key"""
        name = model_data.get("name", "")
        api_key = model_data.get("api_key", "")
        
        # 如果 api_key 以 $ 开头，从环境变量读取
        if api_key.startswith("$"):
            env_var = api_key[1:]  # 去掉 $ 前缀
            api_key = os.environ.get(env_var, "")
        # 如果 api_key 为空字符串，也尝试从环境变量读取
        elif not api_key:
            # 尝试常见的环境变量名
            for env_name in [
                f"{name.upper()}_API_KEY",
                f"{name.upper().replace('-', '_')}_API_KEY",
                "DEEPSEEK_API_KEY",
                "MIMO_API_KEY",
            ]:
                if env_name in os.environ:
                    api_key = os.environ[env_name]
                    break
        
        # 更新 model_data 中的 api_key
        model_data["api_key"] = api_key
        return ModelConfig(**model_data)

    def _create_default(self) -> None:
        self.models = [
            ModelConfig(
                name="deepseek",
                provider="openai",
                base_url="https://api.deepseek.com/v1",
                model_name="deepseek-chat",
                api_key="",  # 请在启动后用 /add_model 添加，或手动修改此文件
                description="DeepSeek 对话模型",
            ),
        ]
        self.current = "deepseek"
        self.save()
        logger.info(f"已创建默认 {self.models_file.name}")
        logger.info("运行 /add_model 添加你的模型，或直接编辑该文件")

    def save(self) -> None:
        """保存 models.json"""
        data = {
            "current": self.current,
            "models": [asdict(m) for m in self.models],
        }
        with open(self.models_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ---------- 查询 ----------

    def get_model(self, name: str) -> ModelConfig | None:
        for m in self.models:
            if m.name == name:
                return m
        return None

    def list_models(self) -> list[ModelConfig]:
        return list(self.models)

    # ---------- 修改 ----------

    def add_model(self, cfg: ModelConfig) -> tuple[bool, str]:
        if self.get_model(cfg.name):
            return False, f"模型名 '{cfg.name}' 已存在，用 /rm_model 删除后再添加"
        self.models.append(cfg)
        if not self.current:
            self.current = cfg.name
        self.save()
        return True, f"已添加: {cfg.display()}"

    def remove_model(self, name: str) -> tuple[bool, str]:
        if not self.get_model(name):
            return False, f"未找到模型 '{name}'"
        self.models = [m for m in self.models if m.name != name]
        if self.current == name:
            self.current = self.models[0].name if self.models else ""
        self.save()
        return True, f"已删除: {name}"

    def set_current(self, name: str) -> tuple[bool, str]:
        if not self.get_model(name):
            return False, f"未找到模型 '{name}'"
        self.current = name
        self.save()
        return True, f"当前模型: {name}"


# ---------- 全局入口 ----------

def load_settings(models_file: str | Path | None = None) -> Settings:
    """加载配置（常用入口）"""
    if models_file is None:
        models_file = Path(__file__).resolve().parent.parent.parent / "models.json"
    else:
        models_file = Path(models_file)
    return Settings(models_file)
