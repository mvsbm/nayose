import { presentError } from "./api-error.js";
import { CommandRegistry, createWorkbenchCommands, type CommandContext } from "./commands.js";
import { disposable, type DisposableStore } from "./lifecycle.js";
import type { WorkbenchPort } from "./port.js";
import { ROUTES } from "./routes.js";
import { esc } from "./schema.js";
import type { Store } from "./store.js";

interface PaletteOptions {
  port: WorkbenchPort;
  store: Store;
  lifetime: DisposableStore;
  capabilities: { admin: boolean; dataEntry: boolean };
  toggleNavigation: () => void;
}

interface PaletteItem {
  label: string;
  meta: string;
  go: () => void;
}

export function mountPalette(options: PaletteOptions): void {
  const { port, store, lifetime, capabilities, toggleNavigation } = options;
  const registry = new CommandRegistry();
  lifetime.add(registry.registerAll(createWorkbenchCommands(ROUTES)));
  const palette = document.querySelector<HTMLElement>("#palette")!;
  const input = document.querySelector<HTMLInputElement>("#palette-input")!;
  const list = document.querySelector<HTMLUListElement>("#palette-results")!;
  const status = document.querySelector<HTMLElement>("#palette-stat")!;
  let items: PaletteItem[] = [];
  let activeIndex = -1;
  let request = 0;
  let restoreFocus: HTMLElement | null = null;

  const close = (): void => {
    request += 1;
    palette.classList.remove("open");
    palette.hidden = true;
    activeIndex = -1;
    const target = restoreFocus?.isConnected ? restoreFocus : document.querySelector<HTMLElement>("#cmd");
    restoreFocus = null;
    target?.focus();
  };
  const context = (): CommandContext => ({
    t: (key) => store.t(key),
    closePalette: close,
    navigate: (hash) => { location.hash = hash; },
    toggleNavigation,
    setTheme: (theme) => store.setTheme(theme),
    setDensity: (density) => store.setDensity(density),
    announce: (message) => { status.textContent = message; },
    capabilities,
  });
  const render = (): void => {
    list.innerHTML = items.map((item, index) => `<li role="option" id="p-${index}" aria-selected="${index === activeIndex}" data-i="${index}"><span>${esc(item.label)}</span><span class="muted">${esc(item.meta)}</span></li>`).join("");
    input.setAttribute("aria-activedescendant", activeIndex >= 0 ? `p-${activeIndex}` : "");
    list.querySelector(`[data-i="${activeIndex}"]`)?.scrollIntoView({ block: "nearest" });
  };
  const search = async (value: string): Promise<void> => {
    const currentRequest = ++request;
    const query = value.trim().toLocaleLowerCase();
    const next: PaletteItem[] = registry.list(context(), query).map((command) => ({
      label: command.label,
      meta: command.meta,
      go: () => registry.execute(command.id, context()),
    }));
    if (value.length >= 2) {
      status.textContent = store.t("searching");
      try {
        const result = await port.entities(value);
        if (currentRequest !== request) return;
        result.rows.forEach((entity) => next.push({
          label: `${entity.name_kanji} ${entity.name_kana ?? ""}`,
          meta: entity.nayose_id,
          go: () => { close(); location.hash = `#/entity/${encodeURIComponent(entity.nayose_id)}`; },
        }));
        status.textContent = store.t("count", { n: result.rows.length });
      } catch (error) {
        if (currentRequest !== request) return;
        status.textContent = `${store.t("error")}: ${presentError(error, store.t.bind(store))}`;
      }
    }
    if (currentRequest !== request) return;
    items = next.slice(0, 14);
    activeIndex = items.length ? 0 : -1;
    render();
  };
  const open = (): void => {
    const active = document.activeElement as HTMLElement | null;
    const opener = document.querySelector<HTMLElement>("#cmd");
    restoreFocus = active && active !== document.body && active !== document.documentElement && typeof active.focus === "function" ? active : opener;
    palette.classList.add("open");
    palette.hidden = false;
    input.value = "";
    status.textContent = "";
    void search("");
    input.focus();
  };
  const onKeydown = (event: KeyboardEvent): void => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      open();
    } else if (!palette.hidden && event.key === "Tab") {
      event.preventDefault();
      input.focus();
    } else if (!palette.hidden && event.key === "Escape") {
      close();
    }
  };
  const onInputKeydown = (event: KeyboardEvent): void => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (items.length) {
        activeIndex = (activeIndex + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
        render();
      }
    } else if (event.key === "Enter") {
      event.preventDefault();
      if (activeIndex >= 0) items[activeIndex]?.go();
    }
  };
  const onListClick = (event: MouseEvent): void => {
    const index = Number((event.target as HTMLElement).closest<HTMLElement>("li")?.dataset.i);
    items[index]?.go();
  };
  const onBackdropClick = (event: MouseEvent): void => { if (event.target === palette) close(); };
  const onInput = (): void => { void search(input.value); };
  const commandButton = document.querySelector<HTMLElement>("#cmd");
  document.addEventListener("keydown", onKeydown);
  input.addEventListener("input", onInput);
  input.addEventListener("keydown", onInputKeydown);
  list.addEventListener("click", onListClick);
  palette.addEventListener("click", onBackdropClick);
  commandButton?.addEventListener("click", open);
  lifetime.add(disposable(() => document.removeEventListener("keydown", onKeydown)));
  lifetime.add(disposable(() => input.removeEventListener("input", onInput)));
  lifetime.add(disposable(() => input.removeEventListener("keydown", onInputKeydown)));
  lifetime.add(disposable(() => list.removeEventListener("click", onListClick)));
  lifetime.add(disposable(() => palette.removeEventListener("click", onBackdropClick)));
  if (commandButton) lifetime.add(disposable(() => commandButton.removeEventListener("click", open)));
}
