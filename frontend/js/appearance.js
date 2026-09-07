export function resolveTheme(theme, prefersDark) {
    return theme === "auto" ? (prefersDark ? "dark" : "light") : theme;
}
export function prefersDarkTheme() {
    return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}
export function applyAppearance(theme, density) {
    const effective = resolveTheme(theme, prefersDarkTheme());
    document.documentElement.dataset.theme = effective;
    document.documentElement.dataset.density = density;
    const meta = document.querySelector('meta[name="theme-color"]');
    meta?.setAttribute("content", effective === "dark" ? "#0b1220" : "#f6f7fb");
    return effective;
}
