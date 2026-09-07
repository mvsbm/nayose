// Shared presentation helpers — transport (fetch + auth) lives in infra.ts,
// the ONE server seam. Keep browser animation isolated from view modules.
export type { RunInfo } from "./port.js";

export interface FlipAnimator {
  capture(root: ParentNode): void;
  play(root: ParentNode): void;
}

export function createFlipAnimator(selector: string): FlipAnimator {
  let previous = new Map<number, DOMRect>();
  const reducedMotion = (): boolean => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return {
    capture(root: ParentNode): void {
      previous = new Map([...root.querySelectorAll<HTMLElement>(selector)].map((el, index) => [index, el.getBoundingClientRect()]));
    },
    play(root: ParentNode): void {
      const current = [...root.querySelectorAll<HTMLElement>(selector)];
      if (reducedMotion() || typeof window.Element?.prototype.animate !== "function") {
        previous.clear();
        return;
      }
      current.forEach((el, index) => {
        const first = previous.get(index);
        if (!first) return;
        const last = el.getBoundingClientRect();
        const dx = first.left - last.left;
        const dy = first.top - last.top;
        const scaleX = last.width ? first.width / last.width : 1;
        const scaleY = last.height ? first.height / last.height : 1;
        if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(scaleX - 1) < 0.005 && Math.abs(scaleY - 1) < 0.005) return;
        el.animate(
          [{ transform: `translate(${dx}px, ${dy}px) scale(${scaleX}, ${scaleY})` }, { transform: "none" }],
          { duration: 240, easing: "cubic-bezier(.2,.8,.2,1)" },
        );
      });
      previous.clear();
    },
  };
}