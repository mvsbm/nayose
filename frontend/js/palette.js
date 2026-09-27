import { presentError } from "./api-error.js";
import { CommandRegistry, createWorkbenchCommands } from "./commands.js";
import { disposable } from "./lifecycle.js";
import { ROUTES } from "./routes.js";
import { esc } from "./schema.js";
export function mountPalette(options) {
    const { port, store, lifetime, capabilities, toggleNavigation } = options;
    const registry = new CommandRegistry();
    lifetime.add(registry.registerAll(createWorkbenchCommands(ROUTES)));
    const palette = document.querySelector("#palette");
    const input = document.querySelector("#cmd");
    const list = document.querySelector("#palette-results");
    const status = document.querySelector("#palette-stat");
    let items = [];
    let activeIndex = -1;
    let request = 0;
    const close = () => {
        request += 1;
        palette.classList.remove("open");
        palette.hidden = true;
        input.setAttribute("aria-expanded", "false");
        input.setAttribute("aria-activedescendant", "");
        activeIndex = -1;
    };
    const context = () => ({
        t: (key) => store.t(key),
        closePalette: close,
        navigate: (hash) => { location.hash = hash; },
        toggleNavigation,
        setTheme: (theme) => store.setTheme(theme),
        setDensity: (density) => store.setDensity(density),
        announce: (message) => { status.textContent = message; },
        capabilities,
    });
    const render = () => {
        list.innerHTML = items.map((item, index) => `<li role="option" id="p-${index}" aria-selected="${index === activeIndex}" data-i="${index}"><span>${esc(item.label)}</span><span class="muted">${esc(item.meta)}</span></li>`).join("");
        input.setAttribute("aria-activedescendant", activeIndex >= 0 ? `p-${activeIndex}` : "");
        list.querySelector(`[data-i="${activeIndex}"]`)?.scrollIntoView({ block: "nearest" });
    };
    const search = async (value) => {
        const currentRequest = ++request;
        const query = value.trim().toLocaleLowerCase();
        const next = registry.list(context(), query).map((command) => ({
            label: command.label,
            meta: command.meta,
            go: () => registry.execute(command.id, context()),
        }));
        if (value.length >= 2) {
            status.textContent = store.t("searching");
            try {
                const result = await port.entities(value);
                if (currentRequest !== request)
                    return;
                result.rows.forEach((entity) => next.push({
                    label: `${entity.name_kanji} ${entity.name_kana ?? ""}`,
                    meta: entity.nayose_id,
                    go: () => { close(); location.hash = `#/entity/${encodeURIComponent(entity.nayose_id)}`; },
                }));
                status.textContent = store.t("count", { n: result.rows.length });
            }
            catch (error) {
                if (currentRequest !== request)
                    return;
                status.textContent = `${store.t("error")}: ${presentError(error, store.t.bind(store))}`;
            }
        }
        if (currentRequest !== request)
            return;
        items = next.slice(0, 14);
        activeIndex = items.length ? 0 : -1;
        render();
    };
    const open = () => {
        palette.classList.add("open");
        palette.hidden = false;
        input.setAttribute("aria-expanded", "true");
        status.textContent = "";
        void search(input.value);
        input.focus();
    };
    const onKeydown = (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
            event.preventDefault();
            open();
        }
        else if (!palette.hidden && event.key === "Escape") {
            close();
        }
    };
    const onInputKeydown = (event) => {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (items.length) {
                activeIndex = (activeIndex + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
                render();
            }
        }
        else if (event.key === "Enter") {
            event.preventDefault();
            if (activeIndex >= 0)
                items[activeIndex]?.go();
        }
        else if (event.key === "Escape") {
            event.preventDefault();
            close();
        }
    };
    const onListClick = (event) => {
        const index = Number(event.target.closest("li")?.dataset.i);
        items[index]?.go();
    };
    const onInput = () => { if (palette.hidden)
        open();
    else
        void search(input.value); };
    const onInputFocus = () => { if (palette.hidden)
        open(); };
    const commandCenter = document.querySelector("#command-center");
    const onPointerdown = (event) => {
        const target = event.target;
        const isDomNode = target !== null && typeof target === "object" && "nodeType" in target;
        if (!palette.hidden && isDomNode && commandCenter && !commandCenter.contains(target))
            close();
    };
    const commandButton = document.querySelector("#cmd");
    document.addEventListener("keydown", onKeydown);
    document.addEventListener("pointerdown", onPointerdown);
    input.addEventListener("input", onInput);
    input.addEventListener("keydown", onInputKeydown);
    input.addEventListener("focus", onInputFocus);
    list.addEventListener("click", onListClick);
    commandButton?.addEventListener("click", open);
    lifetime.add(disposable(() => document.removeEventListener("keydown", onKeydown)));
    lifetime.add(disposable(() => document.removeEventListener("pointerdown", onPointerdown)));
    lifetime.add(disposable(() => input.removeEventListener("input", onInput)));
    lifetime.add(disposable(() => input.removeEventListener("keydown", onInputKeydown)));
    lifetime.add(disposable(() => input.removeEventListener("focus", onInputFocus)));
    lifetime.add(disposable(() => list.removeEventListener("click", onListClick)));
    if (commandButton)
        lifetime.add(disposable(() => commandButton.removeEventListener("click", open)));
}
