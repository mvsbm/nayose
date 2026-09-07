export function createFlipAnimator(selector) {
    let previous = new Map();
    const reducedMotion = () => typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    return {
        capture(root) {
            previous = new Map([...root.querySelectorAll(selector)].map((el, index) => [index, el.getBoundingClientRect()]));
        },
        play(root) {
            const current = [...root.querySelectorAll(selector)];
            if (reducedMotion() || typeof window.Element?.prototype.animate !== "function") {
                previous.clear();
                return;
            }
            current.forEach((el, index) => {
                const first = previous.get(index);
                if (!first)
                    return;
                const last = el.getBoundingClientRect();
                const dx = first.left - last.left;
                const dy = first.top - last.top;
                const scaleX = last.width ? first.width / last.width : 1;
                const scaleY = last.height ? first.height / last.height : 1;
                if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(scaleX - 1) < 0.005 && Math.abs(scaleY - 1) < 0.005)
                    return;
                el.animate([{ transform: `translate(${dx}px, ${dy}px) scale(${scaleX}, ${scaleY})` }, { transform: "none" }], { duration: 240, easing: "cubic-bezier(.2,.8,.2,1)" });
            });
            previous.clear();
        },
    };
}
