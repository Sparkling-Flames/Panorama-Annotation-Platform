# 第三方代码复用清单

当前平台没有复制、打包或运行 Label Studio、HOHONET、Three.js 或其用户脚本代码。

| 来源 | 本次用途 | 复制/分发状态 | 许可证结论 |
| --- | --- | --- | --- |
| `HOHONET/tools/label_studio/label_studio_view_config.xml` | 只读核对 Difficulty、Model Issue 的现行字段与稳定代码 | 未复制 | 邻近目录未发现可覆盖该文件的许可证；不得复制或分发 |
| `HOHONET/tools/label_studio/label_studio_view_config_manual.xml` | 只读核对 Manual 模式不包含 Model Issue | 未复制 | 同上 |
| `HOHONET/tools/label_studio/localized/en/label_studio_view_config_en.xml` | 只读核对中英文语义对应 | 未复制 | 同上 |
| `HOHONET/tools/label_studio/localized/en/label_studio_view_config_manual_en.xml` | 只读核对 Manual 英文语义 | 未复制 | 同上 |

平台在 OpenSpec 和自身版本化 MetaSchema 中独立保存已确认的领域代码与文案，不读取上述文件，也不兼容 Label Studio XML 数据形状。未来若要复用任何第三方实现，必须先逐文件记录来源提交、许可证、必要声明和最小复制范围，再修改本清单。
