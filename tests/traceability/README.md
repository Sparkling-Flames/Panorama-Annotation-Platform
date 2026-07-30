# OpenSpec 测试追踪约定

`openspec_scenarios.json` 是当前 change 的 requirement/scenario 覆盖清单，不是第二套需求或任务系统。产品语义仍只来自 OpenSpec；该清单仅把每个场景映射到稳定测试 ID。

- Requirement ID：`PAP-<CAPABILITY_PREFIX>-REQ-<NNN>`。
- Scenario/Test ID：`PAP-<CAPABILITY_PREFIX>-SC-<NNN>`。
- 已分配 ID 不得因排序或文案维护而复用；新增场景使用该 capability 的下一个编号。
- `planned` 只表示已建立映射，不表示测试已实现。
- 测试落地后改为 `implemented`，并在 `test_files` 中列出真实测试文件；Python 测试、Vitest 和 Playwright 测试名称或相邻注释应包含对应 Test ID。
- 一个场景可映射多个测试 ID，但每个 Test ID 全局唯一。

校验命令：

```powershell
.\.venv\Scripts\python.exe scripts\validate_openspec_traceability.py
```

校验器直接读取 `openspec/changes/build-panorama-annotation-platform-v1/specs/*/spec.md`，任何缺失 capability、requirement、scenario 或测试 ID 都会失败。`--write-template` 只用于首次生成且拒绝覆盖已有清单。
