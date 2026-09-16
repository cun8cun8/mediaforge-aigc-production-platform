"""Shared stage definitions for the MediaForge production control plane."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductionStage:
    key: str
    label: str
    description: str
    workspace_tab: str


PRODUCTION_STAGES: tuple[ProductionStage, ...] = (
    ProductionStage("script", "需求与剧本", "故事依据、剧本与制作简报", "script"),
    ProductionStage("storyboard", "分镜", "镜头卡、时长与视觉叙事", "inspector"),
    ProductionStage("assets", "资产", "角色、参考资产与镜头规格", "assets"),
    ProductionStage("video", "视频", "生成结果、质量检查与镜头审核", "jobs"),
    ProductionStage("postproduction", "后期", "时间线、音频、字幕与合成成片", "assets"),
    ProductionStage("delivery", "交付", "合规、交付包、发布与分发", "audit"),
)


def stage_index(stage_key: str) -> int:
    for index, stage in enumerate(PRODUCTION_STAGES):
        if stage.key == stage_key:
            return index
    raise ValueError(f"unknown production stage: {stage_key}")


def stage_definition(stage_key: str) -> ProductionStage:
    return PRODUCTION_STAGES[stage_index(stage_key)]


def downstream_stages(stage_key: str, *, include_current: bool = True) -> tuple[ProductionStage, ...]:
    index = stage_index(stage_key)
    return PRODUCTION_STAGES[index if include_current else index + 1:]
