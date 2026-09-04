export class Store {
    run = "";
    theme = (typeof document !== "undefined" ? document.documentElement.dataset.theme : "light") === "dark" ? "dark" : "light";
    checked = new Set();
    runs = [];
    listeners = new Map();
    history = [];
    future = [];
    on(key, fn) {
        (this.listeners.get(key) ?? this.listeners.set(key, new Set()).get(key)).add(fn);
    }
    emit(key) {
        this.listeners.get(key)?.forEach((fn) => fn());
    }
    setRun(id) {
        this.run = id;
        this.emit("run");
    }
    setTheme(theme) {
        this.theme = theme;
        document.documentElement.dataset.theme = theme;
        this.emit("theme");
    }
    toggleTheme() {
        this.setTheme(this.theme === "dark" ? "light" : "dark");
    }
    toggleCheckedKey(key) {
        const had = this.checked.has(key);
        const apply = (add) => (add ? this.checked.add(key) : this.checked.delete(key));
        const command = {
            label: had ? "既読を戻す" : "既読にする",
            undo: () => { apply(had); this.emit("checked"); },
            redo: () => { apply(!had); this.emit("checked"); },
        };
        apply(!had);
        this.history.push(command);
        this.future = [];
        this.emit("checked");
    }
    undo() {
        const cmd = this.history.pop();
        if (!cmd)
            return false;
        cmd.undo();
        this.future.push(cmd);
        return true;
    }
    redo() {
        const cmd = this.future.pop();
        if (!cmd)
            return false;
        cmd.redo();
        this.history.push(cmd);
        return true;
    }
    clearRunState() {
        this.checked.clear();
        this.history = [];
        this.future = [];
        this.emit("checked");
    }
    read(key) {
        try {
            return localStorage.getItem(key);
        }
        catch {
            return null;
        }
    }
    write(key, value) {
        try {
            localStorage.setItem(key, value);
        }
        catch {
            /* private mode / storage disabled */
        }
    }
}
