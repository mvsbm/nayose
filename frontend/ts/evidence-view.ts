import type { FieldComparison } from "./port.js";
import type { Store } from "./store.js";
import { esc } from "./schema.js";

export const FIELD_GROUPS = {
  identity: ["party_type", "name_kanji", "name_kana", "name_romaji", "date_birth"],
  contact: ["postal_code", "address", "phone", "email"],
  source: ["record_id", "policy_id", "agency_id", "channel", "observed_at"],
};

export const percent = (n: number): string => `${(n * 100).toFixed(1)}%`;
export const value = (store: Store, v: unknown): string => esc(v === null || v === undefined || v === "" ? store.t("missingValue") : v);

export function recordFields(store: Store, record: Record<string, unknown>, fields: string[]): string {
  return `<dl class="field-grid">${fields.map(f => `<div><dt>${esc(store.t(f))}</dt><dd>${value(store, record[f])}</dd></div>`).join("")}</dl>`;
}

export function comparisonTable(store: Store, comparisons: Record<string, FieldComparison>, leftLabel = "input"): string {
  return `<div class="table-scroll" tabindex="0" role="region" aria-label="${esc(store.t("comparison"))}"><table>
    <caption>${esc(store.t("comparison"))}</caption>
    <thead><tr>${["field", leftLabel, "observed", "comparison", "normalizedValues"].map(k => `<th scope="col">${esc(store.t(k))}</th>`).join("")}</tr></thead>
    <tbody>${Object.entries(comparisons).map(([field, c]) => `<tr>
      <th scope="row">${esc(store.t(field))}</th><td>${value(store, c.left)}</td><td>${value(store, c.right)}</td>
      <td><span class="evidence-state" data-status="${esc(c.status)}">${c.status === "different" ? "≠" : c.status === "missing" ? "—" : "="} ${esc(store.t(c.status))}</span></td>
      <td>${value(store, c.left_normalized)} / ${value(store, c.right_normalized)}</td></tr>`).join("")}</tbody>
  </table></div>`;
}
