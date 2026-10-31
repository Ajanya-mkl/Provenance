import { motion, useReducedMotion } from "framer-motion";
import { Ban, Check, CircleCheck, Clock, Inbox, Lock, X } from "lucide-react";
import { useId, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router";

import { ActivityFeed } from "../components/blocks";
import { Modal } from "../components/Dialogs";
import { EmptyState, ErrorState, LoadingBlock, PageHeader, Segmented } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { ACTION_LABELS, dateTime, percent, quarterFromIso, shortId } from "../lib/format";
import { useSupplierName } from "../lib/names";
import { useApprovals } from "../lib/pending";
import type { ApprovalItem } from "../lib/types";

type Filter = "pending" | "decided" | "all";
type Decision = { item: ApprovalItem; kind: "approve" | "reject" };

const NAME_KEY = "provenance.reviewer";

function rememberedName(): string {
  try {
    return localStorage.getItem(NAME_KEY) ?? "";
  } catch {
    return "";
  }
}

const STATUS_STYLE: Record<string, string> = {
  proposed: "text-tier-medium",
  applied: "text-tier-low",
  approved: "text-tier-low",
  rejected: "text-ink-3",
};

function DecisionForm({
  decision,
  onDone,
  onCancel,
}: {
  decision: Decision;
  onDone: (message: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(rememberedName);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [touched, setTouched] = useState(false);
  const nameId = useId();
  const reasonId = useId();
  const errorId = useId();
  const reject = decision.kind === "reject";
  const nameMissing = !name.trim();
  const reasonMissing = reject && !reason.trim();
  const nameOf = useSupplierName();

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (nameMissing || reasonMissing) return;
    setBusy(true);
    setError(null);
    try {
      const result = reject
        ? await api.reject(decision.item.action_id, name.trim(), reason.trim())
        : await api.approve(decision.item.action_id, name.trim(), reason.trim());
      try {
        localStorage.setItem(NAME_KEY, name.trim());
      } catch {
        // Remembering the name is a convenience only.
      }
      const effect = "effect" in result ? result.effect : null;
      onDone(
        reject
          ? "Rejected. Nothing was applied."
          : effect
            ? `Approved and applied to ${effect}.`
            : "Approved. No action was needed.",
      );
    } catch (caught) {
      setError(caught instanceof ApiError ? caught : new ApiError(0, String(caught)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate className="space-y-4">
      <p className="text-[15px] text-ink-2">
        {ACTION_LABELS[decision.item.action_type]} for{" "}
        <span className="font-semibold text-ink">
          {nameOf(decision.item.supplier_key)} · {quarterFromIso(decision.item.quarter)}
        </span>
        . {reject ? "Nothing will be applied." : "Approving applies it immediately."}
      </p>
      <div>
        <label htmlFor={nameId} className="mb-1.5 block text-[13px] font-medium text-ink-2">
          Reviewer name <span className="text-danger">(required)</span>
        </label>
        <input
          id={nameId}
          value={name}
          onChange={(event) => setName(event.target.value)}
          autoFocus
          autoComplete="name"
          aria-invalid={touched && nameMissing}
          aria-describedby={touched && nameMissing ? `${nameId}-err` : undefined}
          className="w-full rounded-[14px] bg-[color-mix(in_srgb,var(--ink)_8%,transparent)] px-4 py-2.5 text-[15px] text-ink shadow-[inset_0_0_0_1px_var(--line)] outline-none focus:shadow-[inset_0_0_0_2px_var(--accent)]"
        />
        {touched && nameMissing && (
          <p id={`${nameId}-err`} className="mt-1 text-[13px] text-danger">
            Enter your name so the decision is recorded against it.
          </p>
        )}
      </div>
      <div>
        <label htmlFor={reasonId} className="mb-1.5 block text-[13px] font-medium text-ink-2">
          Reason {reject ? <span className="text-danger">(required)</span> : "(optional)"}
        </label>
        <textarea
          id={reasonId}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          rows={3}
          maxLength={500}
          aria-invalid={touched && reasonMissing}
          className="w-full resize-none rounded-[14px] bg-[color-mix(in_srgb,var(--ink)_8%,transparent)] px-4 py-2.5 text-[15px] text-ink shadow-[inset_0_0_0_1px_var(--line)] outline-none focus:shadow-[inset_0_0_0_2px_var(--accent)]"
        />
        {touched && reasonMissing && (
          <p className="mt-1 text-[13px] text-danger">Say why, so the next run can recall it.</p>
        )}
      </div>
      {error && (
        <div id={errorId} role="alert" className="flex gap-3 rounded-[14px] bg-danger/12 p-3 text-[14px]">
          {error.code === "locked" ? (
            <Lock aria-hidden size={18} className="mt-0.5 shrink-0 text-danger" />
          ) : (
            <X aria-hidden size={18} className="mt-0.5 shrink-0 text-danger" />
          )}
          <div>
            <p className="font-semibold text-ink">
              {error.code === "locked" ? "Warehouse is busy" : "Couldn't record the decision"}
            </p>
            <p className="text-ink-2">{error.message}</p>
          </div>
        </div>
      )}
      <div className="flex justify-end gap-2 pt-2">
        <button type="button" onClick={onCancel} className="glass-pill px-4 py-2 text-sm">
          Cancel
        </button>
        <button
          type="submit"
          disabled={busy}
          aria-describedby={error ? errorId : undefined}
          className={`glass-pill px-4 py-2 text-sm font-semibold ${reject ? "" : "pill-accent"}`}
        >
          {busy ? "Recording…" : error?.code === "locked" ? "Try again" : reject ? "Reject" : "Approve and apply"}
        </button>
      </div>
    </form>
  );
}

function ApprovalRow({ item, onDecide }: { item: ApprovalItem; onDecide: (decision: Decision) => void }) {
  const pending = item.status === "proposed";
  const nameOf = useSupplierName();
  const StatusIcon = pending ? Clock : item.status === "rejected" ? Ban : CircleCheck;
  return (
    <li className="border-b border-line px-4 py-4 last:border-0">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="min-w-[200px] flex-1">
          <p className="text-[15px] font-semibold">
            {nameOf(item.supplier_key)}{" "}
            <span className="tabular font-normal text-ink-2">· {quarterFromIso(item.quarter)}</span>
          </p>
          <p className="text-[13px] text-ink-3">
            Action {shortId(item.action_id)} · proposed {dateTime(item.proposed_at)}
            {item.grounded_ratio != null && <> · grounded {percent(item.grounded_ratio)}</>}
          </p>
        </div>
        <p className="w-[150px] text-[15px] font-medium">{ACTION_LABELS[item.action_type]}</p>
        <p className={`w-[150px] text-[14px] font-semibold capitalize ${STATUS_STYLE[item.status] ?? ""}`}>
          <span className="inline-flex items-center gap-1.5">
            <StatusIcon aria-hidden size={15} />
            {item.status}
          </span>
          {item.decided_by && <span className="block text-[12px] font-normal text-ink-3 normal-case">by {item.decided_by}</span>}
        </p>
        <div className="flex gap-2">
          <Link to={`/investigations/${encodeURIComponent(item.event_id)}`} className="glass-pill px-3.5 py-2 text-[13px]">
            Report
          </Link>
          {pending && (
            <>
              <button type="button" onClick={() => onDecide({ item, kind: "reject" })} className="glass-pill px-3.5 py-2 text-[13px]">
                <X aria-hidden size={15} /> Reject
              </button>
              <button
                type="button"
                onClick={() => onDecide({ item, kind: "approve" })}
                className="glass-pill pill-accent px-3.5 py-2 text-[13px]"
              >
                <Check aria-hidden size={15} /> Approve
              </button>
            </>
          )}
        </div>
      </div>
      {item.audit.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-[13px] text-ink-2 hover:text-ink">
            Audit trail ({item.audit.length})
          </summary>
          <div className="mt-1 pl-1">
            <ActivityFeed entries={[...item.audit].reverse()} />
          </div>
        </details>
      )}
    </li>
  );
}

export function ApprovalsPage() {
  const approvals = useApprovals();
  const [filter, setFilter] = useState<Filter>("pending");
  const [decision, setDecision] = useState<Decision | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const reduce = useReducedMotion();

  const items = (approvals.data?.items ?? []).filter((item) =>
    filter === "all" ? true : filter === "pending" ? item.status === "proposed" : item.status !== "proposed",
  );

  return (
    <>
      <PageHeader
        title="Approvals"
        meta="Nothing the agent proposes takes effect until a named reviewer approves it."
      />
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <Segmented<Filter>
          label="Show"
          value={filter}
          onChange={setFilter}
          options={[
            { value: "pending", label: `Pending${approvals.data ? ` (${approvals.data.pending})` : ""}` },
            { value: "decided", label: "Decided" },
            { value: "all", label: "All" },
          ]}
        />
        <div aria-live="polite" className="min-h-[36px]">
          {notice && (
            <motion.p
              initial={reduce ? false : { opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              className="glass-pill px-4 py-2 text-sm font-medium"
            >
              <CircleCheck aria-hidden size={16} className="text-tier-low" /> {notice}
            </motion.p>
          )}
        </div>
      </div>

      <section aria-label="Action queue" className="glass p-2">
        {approvals.error ? (
          <ErrorState error={approvals.error} onRetry={approvals.reload} />
        ) : approvals.loading ? (
          <div className="p-4">
            <LoadingBlock rows={6} label="Loading approvals" />
          </div>
        ) : items.length === 0 ? (
          <EmptyState icon={<Inbox size={22} />} title={filter === "pending" ? "Nothing waiting" : "No actions here"}>
            {filter === "pending"
              ? "Grounded investigations create proposed actions here. Ungrounded reports never do."
              : "Decisions appear here once a reviewer approves or rejects an action."}
          </EmptyState>
        ) : (
          <ul>
            {items.map((item) => (
              <ApprovalRow
                key={item.action_id}
                item={item}
                onDecide={(next) => {
                  setNotice(null);
                  setDecision(next);
                }}
              />
            ))}
          </ul>
        )}
      </section>

      <Modal
        open={decision !== null}
        onClose={() => setDecision(null)}
        title={decision?.kind === "reject" ? "Reject action" : "Approve action"}
      >
        {decision && (
          <DecisionForm
            key={`${decision.item.action_id}-${decision.kind}`}
            decision={decision}
            onCancel={() => setDecision(null)}
            onDone={(message) => {
              setDecision(null);
              setNotice(message);
              approvals.reload();
            }}
          />
        )}
      </Modal>
    </>
  );
}
