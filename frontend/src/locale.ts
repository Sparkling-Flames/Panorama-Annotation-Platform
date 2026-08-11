export type SupportedLocale = "en" | "zh-CN";

export function formatLocalTimestamp(value: string, locale: SupportedLocale): string {
  return new Intl.DateTimeFormat(locale, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    second: "2-digit",
    timeZoneName: "longOffset",
    year: "numeric",
  }).format(new Date(value));
}
