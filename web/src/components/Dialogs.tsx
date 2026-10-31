import { motion, useReducedMotion } from "framer-motion";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useId, useRef } from "react";

/**
 * Native <dialog> for focus trapping, Escape and an inert background; motion
 * only animates the content in. Shared by the side sheet and the modal.
 */
function useDialog(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      try {
        dialog.showModal();
      } catch {
        dialog.setAttribute("open", "");
      }
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const handleCancel = (event: Event) => {
      event.preventDefault();
      onClose();
    };
    dialog.addEventListener("cancel", handleCancel);
    return () => dialog.removeEventListener("cancel", handleCancel);
  }, [onClose]);
  return ref;
}

const SPRING = { type: "spring" as const, stiffness: 380, damping: 36, mass: 0.9 };

export function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const ref = useDialog(open, onClose);
  const titleId = useId();
  const reduce = useReducedMotion();
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClick={(event) => event.target === ref.current && onClose()}
      className="fixed inset-0 m-0 ml-auto h-dvh max-h-none w-full max-w-[720px] glass-dialog bg-transparent p-3 text-ink sm:p-4"
    >
      {open && (
        <motion.div
          initial={reduce ? false : { x: 56, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          transition={reduce ? { duration: 0 } : SPRING}
          className="glass-strong flex h-full flex-col overflow-hidden"
        >
          <div className="flex items-center justify-between gap-4 border-b border-line px-6 py-4">
            <h2 id={titleId} className="truncate text-[20px] font-semibold tracking-[-0.02em]">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close details"
              className="glass-pill size-9 justify-center"
            >
              <X aria-hidden size={18} />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto overscroll-contain px-6 py-6">{children}</div>
        </motion.div>
      )}
    </dialog>
  );
}

export function Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const ref = useDialog(open, onClose);
  const titleId = useId();
  const reduce = useReducedMotion();
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className="m-auto w-[min(520px,calc(100vw-32px))] glass-dialog bg-transparent p-0 text-ink"
    >
      {open && (
        <motion.div
          initial={reduce ? false : { y: 16, scale: 0.97, opacity: 0 }}
          animate={{ y: 0, scale: 1, opacity: 1 }}
          transition={reduce ? { duration: 0 } : SPRING}
          className="glass-strong p-7"
        >
          <h2 id={titleId} className="mb-4 text-[20px] font-semibold tracking-[-0.02em]">
            {title}
          </h2>
          {children}
        </motion.div>
      )}
    </dialog>
  );
}
