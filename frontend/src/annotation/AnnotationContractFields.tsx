import {
  SCOPE_REASON_CODES,
  type AnnotationState,
  type MetaContract,
  type MetaOption,
} from "../annotationState";
import type { SupportedLocale } from "../locale";

const ATTEMPT_OPTIONS = [
  ["best_effort_complete", { en: "Best effort complete", "zh-CN": "尽力完成" }],
  ["partial", { en: "Partial", "zh-CN": "部分完成" }],
  ["not_drawable", { en: "Not drawable", "zh-CN": "无法绘制" }],
] as const;
const bilingual = (zh: string, en: string) => ({ en, "zh-CN": zh });
const POC_CONTRACT: MetaContract = {
  copy_version: "poc-bilingual-copy-v0",
  difficulty_options: [],
  schema_version: "poc-contract-v0",
  scope_options: [
    { code: "annotatable", label: bilingual("可标注", "Annotatable") },
    { code: "needs_scope_review", label: bilingual("需范围复核", "Needs scope review") },
    {
      code: "representation_oos",
      label: bilingual("表示范围外", "Representation out of scope"),
    },
  ],
  scope_reason_options: SCOPE_REASON_CODES.map((code) => ({
    code,
    label: bilingual(code, code),
  })),
};

function optionLabel(option: MetaOption, locale: SupportedLocale): string {
  return option.label[locale];
}

export function AnnotationContractFields({
  contract = POC_CONTRACT,
  locale = "zh-CN",
  onChange,
  state,
}: {
  contract?: MetaContract;
  locale?: SupportedLocale;
  onChange: (state: AnnotationState) => void;
  state: AnnotationState;
}) {
  function toggleReason(reason: string): void {
    onChange({
      ...state,
      scope_reason_codes: state.scope_reason_codes.includes(reason)
        ? state.scope_reason_codes.filter((code) => code !== reason)
        : [...state.scope_reason_codes, reason],
      scope_reason_text:
        reason === "other" && state.scope_reason_codes.includes(reason)
          ? ""
          : state.scope_reason_text,
    });
  }

  function toggleDifficulty(code: string): void {
    const current = state.difficulty ?? [];
    const selected = new Set(
      code === "trivial"
        ? current.includes(code)
          ? []
          : [code]
        : current.includes(code)
          ? current.filter((item) => item !== code)
          : [...current.filter((item) => item !== "trivial"), code],
    );
    onChange({
      ...state,
      difficulty: contract.difficulty_options
        .map((option) => option.code)
        .filter((item) => selected.has(item)),
    });
  }

  function toggleModelIssue(code: string): void {
    const current = state.model_issue ?? [];
    const selected = new Set(
      code === "acceptable"
        ? current.includes(code)
          ? []
          : [code]
        : current.includes(code)
          ? current.filter((item) => item !== code)
          : [...current.filter((item) => item !== "acceptable"), code],
    );
    onChange({
      ...state,
      model_issue: (contract.model_issue_options ?? [])
        .map((option) => option.code)
        .filter((item) => selected.has(item)),
    });
  }

  return (
    <>
      <div className="annotation-contract-fields">
        <label>
          Scope / 范围判断
          <select
            aria-label="Scope / 范围判断"
            onChange={(event) => {
              const value = event.currentTarget.value;
              onChange({
                ...state,
                scope_reason_codes:
                  value === "" || value === "annotatable" ? [] : state.scope_reason_codes,
                scope_reason_text:
                  value === "" || value === "annotatable" ? "" : state.scope_reason_text,
                worker_scope_observation: value || null,
              });
            }}
            value={state.worker_scope_observation ?? ""}
          >
            <option value="">请选择 / Select</option>
            {contract.scope_options.map((option) => (
              <option key={option.code} value={option.code}>
                {optionLabel(option, locale)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Geometry attempt / 几何完成度
          <select
            aria-label="Geometry attempt / 几何完成度"
            onChange={(event) =>
              onChange({
                ...state,
                geometry_attempt_reason_text:
                  event.currentTarget.value === "not_drawable"
                    ? state.geometry_attempt_reason_text
                    : "",
                geometry_attempt_status: event.currentTarget.value || null,
              })
            }
            value={state.geometry_attempt_status ?? ""}
          >
            <option value="">请选择 / Select</option>
            {ATTEMPT_OPTIONS.map(([code, label]) => (
              <option key={code} value={code}>
                {label[locale]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p>这是工人的范围观察，不是最终 eligibility。 / Worker observation, not final eligibility.</p>
      {contract.difficulty_options.length > 0 ? (
        <fieldset>
          <legend>困难因素 / Difficulty</legend>
          {contract.difficulty_options.map((option) => (
            <label key={option.code}>
              <input
                checked={(state.difficulty ?? []).includes(option.code)}
                onChange={() => toggleDifficulty(option.code)}
                type="checkbox"
              />
              {optionLabel(option, locale)}
            </label>
          ))}
        </fieldset>
      ) : null}
      {contract.model_issue_options !== undefined ? (
        <fieldset>
          <legend>模型初始化问题 / Model Issue</legend>
          {contract.model_issue_options.map((option) => (
            <label key={option.code}>
              <input
                checked={(state.model_issue ?? []).includes(option.code)}
                onChange={() => toggleModelIssue(option.code)}
                type="checkbox"
              />
              {optionLabel(option, locale)}
            </label>
          ))}
        </fieldset>
      ) : null}
      {state.geometry_attempt_status === "not_drawable" ? (
        <label>
          无法绘制说明 / Not drawable explanation
          <textarea
            aria-label="无法绘制说明 / Not drawable explanation"
            onChange={(event) =>
              onChange({ ...state, geometry_attempt_reason_text: event.currentTarget.value })
            }
            value={state.geometry_attempt_reason_text}
          />
        </label>
      ) : null}
      {state.worker_scope_observation !== null &&
      state.worker_scope_observation !== "annotatable" ? (
        <fieldset>
          <legend>Scope reasons / 范围原因</legend>
          {contract.scope_reason_options.map((option) => (
            <label key={option.code}>
              <input
                checked={state.scope_reason_codes.includes(option.code)}
                onChange={() => toggleReason(option.code)}
                type="checkbox"
              />
              {optionLabel(option, locale)}
            </label>
          ))}
          {state.scope_reason_codes.includes("other") ? (
            <label>
              Other / 其他说明
              <input
                onChange={(event) =>
                  onChange({ ...state, scope_reason_text: event.currentTarget.value })
                }
                type="text"
                value={state.scope_reason_text}
              />
            </label>
          ) : null}
        </fieldset>
      ) : null}
    </>
  );
}
