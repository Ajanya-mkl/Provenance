import { animate, useReducedMotion } from "framer-motion";
import { useEffect, useState } from "react";

/** Animated count-up for KPI values; instant under reduced motion. */
export function useCountUp(target: number | undefined, decimals = 0): string {
  const reduce = useReducedMotion();
  const [value, setValue] = useState(0);
  useEffect(() => {
    if (target === undefined) return;
    if (reduce) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setValue(target);
      return;
    }
    const controls = animate(0, target, {
      duration: 0.7,
      ease: [0.22, 1, 0.36, 1],
      onUpdate: setValue,
    });
    return () => controls.stop();
  }, [target, reduce]);
  return target === undefined ? "–" : value.toFixed(decimals);
}
