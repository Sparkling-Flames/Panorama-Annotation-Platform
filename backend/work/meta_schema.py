from __future__ import annotations

from typing import NotRequired, TypedDict

from django.core.exceptions import ValidationError

POC_META_SCHEMA_VERSION = "poc-contract-v0"
POC_META_COPY_VERSION = "poc-bilingual-copy-v0"
META_SCHEMA_V1 = "annotation-meta-v1"
META_COPY_V1 = "annotation-meta-copy-v1"
SUPPORTED_LOCALES = frozenset({"en", "zh-CN"})

DIFFICULTY_CODES = (
    "trivial",
    "occlusion",
    "low_texture",
    "seam",
    "reflection",
    "low_quality",
)
MODEL_ISSUE_CODES = (
    "acceptable",
    "overextend_adjacent",
    "underextend",
    "over_parsing",
    "corner_drift",
    "corner_duplicate",
    "topology_failure",
    "fail",
)
SCOPE_OBSERVATIONS = ("annotatable", "needs_scope_review", "representation_oos")
SCOPE_REASON_CODES = (
    "non_manhattan",
    "multi_level_floor",
    "multi_level_ceiling",
    "internal_void",
    "camera_cell_ambiguous",
    "portal_ambiguous",
    "insufficient_evidence",
    "severe_image_artifact",
    "other",
)

BilingualLabel = TypedDict("BilingualLabel", {"en": str, "zh-CN": str})


class MetaOption(TypedDict):
    code: str
    label: BilingualLabel


class MetaContractPayload(TypedDict):
    copy_version: str
    difficulty_options: list[MetaOption]
    model_issue_options: NotRequired[list[MetaOption]]
    schema_version: str
    scope_options: list[MetaOption]
    scope_reason_options: list[MetaOption]


def _option(code: str, zh_cn: str, en: str) -> MetaOption:
    return {"code": code, "label": {"en": en, "zh-CN": zh_cn}}


_POC_SCOPE_OPTIONS = (
    _option("annotatable", "可标注", "Annotatable"),
    _option("needs_scope_review", "需范围复核", "Needs scope review"),
    _option("representation_oos", "表示范围外", "Representation out of scope"),
)
_POC_SCOPE_REASON_OPTIONS = tuple(_option(code, code, code) for code in SCOPE_REASON_CODES)
_SCOPE_OPTIONS_V1 = (
    _option("annotatable", "可标注", "Annotatable"),
    _option("needs_scope_review", "需范围复核", "Needs scope review"),
    _option("representation_oos", "当前表示不适用", "Representation not applicable"),
)
_SCOPE_REASON_OPTIONS_V1 = (
    _option("non_manhattan", "非曼哈顿结构", "Non-Manhattan geometry"),
    _option("multi_level_floor", "多层地面", "Multi-level floor"),
    _option("multi_level_ceiling", "多层天花", "Multi-level ceiling"),
    _option("internal_void", "内部空洞", "Internal void"),
    _option("camera_cell_ambiguous", "相机空间不明确", "Camera cell ambiguous"),
    _option("portal_ambiguous", "开口边界不明确", "Portal ambiguous"),
    _option("insufficient_evidence", "证据不足", "Insufficient evidence"),
    _option("severe_image_artifact", "严重图像伪影", "Severe image artifact"),
    _option("other", "其他", "Other"),
)
_DIFFICULTY_OPTIONS = (
    _option("trivial", "非常简单", "Trivial / very easy"),
    _option("occlusion", "遮挡明显", "Occlusion"),
    _option("low_texture", "纹理弱或纯色墙", "Low texture or plain walls"),
    _option("seam", "拼接缝或拉伸明显", "Panorama seam or stretch distortion"),
    _option("reflection", "反光或玻璃干扰", "Reflection or glass interference"),
    _option("low_quality", "模糊、遮罩或低画质", "Blur, mask, or low image quality"),
)
_MODEL_ISSUE_OPTIONS = (
    _option("acceptable", "模型标注质量好", "Model quality acceptable"),
    _option("overextend_adjacent", "跨门扩张：包含相邻空间", "Over-extend to adjacent room"),
    _option("underextend", "漏标：只标了局部", "Under-extend / missing room parts"),
    _option("over_parsing", "过度解析：非结构细节误判", "Over-parsing / ghost geometry"),
    _option("corner_drift", "角点错位或漂移", "Corner drift"),
    _option("corner_duplicate", "角点重复或一角多点", "Duplicate corner points"),
    _option("topology_failure", "拓扑崩溃：配对或闭合失败", "Topological failure"),
    _option("fail", "模型预标注失效", "Prediction failure"),
)


def meta_contract_payload(
    *, schema_version: str | None, copy_version: str | None, task_mode: str | None
) -> MetaContractPayload:
    if schema_version == POC_META_SCHEMA_VERSION:
        if copy_version != POC_META_COPY_VERSION:
            raise ValidationError(
                "Unsupported metadata copy version.", code="task_contract_invalid"
            )
        difficulty_options: list[MetaOption] = []
        scope_options = _POC_SCOPE_OPTIONS
        scope_reason_options = _POC_SCOPE_REASON_OPTIONS
    elif schema_version == META_SCHEMA_V1:
        if copy_version != META_COPY_V1:
            raise ValidationError(
                "Unsupported metadata copy version.", code="task_contract_invalid"
            )
        difficulty_options = list(_DIFFICULTY_OPTIONS)
        scope_options = _SCOPE_OPTIONS_V1
        scope_reason_options = _SCOPE_REASON_OPTIONS_V1
    else:
        raise ValidationError("Unsupported metadata schema version.", code="task_contract_invalid")
    if task_mode not in {"manual", "semi"}:
        raise ValidationError("Unsupported task mode.", code="task_contract_invalid")
    payload: MetaContractPayload = {
        "copy_version": copy_version,
        "difficulty_options": difficulty_options,
        "schema_version": schema_version,
        "scope_options": list(scope_options),
        "scope_reason_options": list(scope_reason_options),
    }
    if schema_version == META_SCHEMA_V1 and task_mode == "semi":
        payload["model_issue_options"] = list(_MODEL_ISSUE_OPTIONS)
    return payload
