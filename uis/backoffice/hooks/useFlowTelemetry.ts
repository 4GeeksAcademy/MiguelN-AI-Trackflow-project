import { useCallback, useEffect, useRef } from "react";
import type { Section } from "@/lib/telemetry-routes";
import { track } from "@/services/telemetry-service";

export type Flow =
  | "inbound_order"
  | "outbound_order"
  | "product_creation"
  | "incident_report"
  | "supplier_creation"
  | "registration"
  | "password_reset"
  | "password_change";

export type FormErrorCode = "required" | "invalid_format" | "out_of_range" | "exceeds_available_stock" | "mismatch" | "other";

type Step = "opened" | "field_edited" | "submit_failed" | "submitted_awaiting_response";
type ExitReason = "navigated_away" | "tab_closed";

type FlowState = { started: boolean; startedAt: number; attempts: number; fields: Set<string>; step: Step };

const fresh = (): FlowState => ({ started: false, startedAt: 0, attempts: 0, fields: new Set(), step: "opened" });

/**
 * Reports one form's lifecycle: `flow_started` when it opens, and exactly one of
 * `flow_completed` or `flow_abandoned` when it ends. Field names are counted,
 * never their values.
 */
export function useFlowTelemetry(flow: Flow, entrySection: Section) {
  const state = useRef<FlowState>(fresh());
  const pendingAbandon = useRef<ReturnType<typeof setTimeout> | null>(null);

  const begin = useCallback(() => {
    if (state.current.started) return;
    state.current = { ...fresh(), started: true, startedAt: performance.now() };
    track("flow_started", { flow, entry_section: entrySection });
  }, [flow, entrySection]);

  const abandon = useCallback(
    (exitReason: ExitReason) => {
      const current = state.current;
      if (!current.started) return;
      state.current = fresh();
      track("flow_abandoned", {
        flow,
        last_step: current.step,
        fields_touched: current.fields.size,
        duration_ms: Math.round(performance.now() - current.startedAt),
        exit_reason: exitReason,
      });
    },
    [flow],
  );

  useEffect(() => {
    // React strict mode remounts synchronously; the deferred abandon is cancelled in that case.
    if (pendingAbandon.current) {
      clearTimeout(pendingAbandon.current);
      pendingAbandon.current = null;
    }
    begin();

    const onPageHide = () => abandon("tab_closed");
    window.addEventListener("pagehide", onPageHide);

    return () => {
      window.removeEventListener("pagehide", onPageHide);
      pendingAbandon.current = setTimeout(() => abandon("navigated_away"), 0);
    };
  }, [begin, abandon]);

  const fieldEdited = useCallback(
    (field: string) => {
      begin();
      state.current.fields.add(field);
      state.current.step = "field_edited";
    },
    [begin],
  );

  const submitAttempted = useCallback(() => {
    begin();
    state.current.attempts += 1;
    state.current.step = "submitted_awaiting_response";
  }, [begin]);

  const submitFailed = useCallback(() => {
    state.current.step = "submit_failed";
  }, []);

  const validationFailed = useCallback(
    (field: string, errorCode: FormErrorCode) => {
      track("form_validation_failed", { flow, field: field.slice(0, 64), error_code: errorCode });
    },
    [flow],
  );

  /** One `form_validation_failed` per field that failed; a confirmation field can only fail by mismatch. */
  const validationFailedFields = useCallback(
    (errors: object) => {
      for (const field of Object.keys(errors)) {
        validationFailed(field, /confirm/i.test(field) ? "mismatch" : "other");
      }
      state.current.step = "submit_failed";
    },
    [validationFailed],
  );

  /** Marks every key of a form patch as edited. */
  const fieldsEdited = useCallback(
    (patch: object) => {
      for (const field of Object.keys(patch)) {
        fieldEdited(field);
      }
    },
    [fieldEdited],
  );

  const completed = useCallback(() => {
    const current = state.current;
    if (!current.started) return;
    state.current = fresh();
    track("flow_completed", {
      flow,
      duration_ms: Math.round(performance.now() - current.startedAt),
      attempts: Math.max(1, current.attempts),
    });
  }, [flow]);

  return { fieldEdited, fieldsEdited, submitAttempted, submitFailed, validationFailed, validationFailedFields, completed };
}
