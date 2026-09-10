import { disposable, DisposableStore } from "./lifecycle.js";
export class CommandRegistry {
    commands = new Map();
    register(command) {
        if (this.commands.has(command.id))
            throw new Error(`Duplicate command: ${command.id}`);
        this.commands.set(command.id, command);
        return disposable(() => this.commands.delete(command.id));
    }
    registerAll(commands) {
        const scope = new DisposableStore();
        commands.forEach((command) => scope.add(this.register(command)));
        return scope;
    }
    list(context, query = "") {
        const normalized = query.trim().toLocaleLowerCase();
        return [...this.commands.values()]
            .filter((command) => command.when?.(context) ?? true)
            .map((command) => ({ ...command, label: context.t(command.labelKey) }))
            .filter((command) => !normalized || `${command.label} ${command.meta}`.toLocaleLowerCase().includes(normalized));
    }
    execute(id, context) {
        const command = this.commands.get(id);
        if (!command || !(command.when?.(context) ?? true))
            return false;
        command.run(context);
        return true;
    }
}
export function createWorkbenchCommands(routes) {
    const navigation = routes
        .filter((route) => route.name !== "entity" && route.name !== "compare")
        .map((route) => ({
        id: `navigate.${route.name}`,
        labelKey: route.titleKey,
        meta: route.path,
        run: (context) => {
            context.closePalette();
            context.navigate(route.path);
        },
        when: route.name === "admin"
            ? (context) => context.capabilities.admin
            : route.name === "data-entry"
                ? (context) => context.capabilities.dataEntry
                : undefined,
    }));
    return [
        ...navigation,
        {
            id: "appearance.theme.light",
            labelKey: "actionThemeLight",
            meta: "action:theme-light",
            run: (context) => { context.setTheme("light"); context.closePalette(); },
        },
        {
            id: "appearance.theme.dark",
            labelKey: "actionThemeDark",
            meta: "action:theme-dark",
            run: (context) => { context.setTheme("dark"); context.closePalette(); },
        },
        {
            id: "appearance.theme.auto",
            labelKey: "actionThemeAuto",
            meta: "action:theme-auto",
            run: (context) => { context.setTheme("auto"); context.closePalette(); },
        },
        {
            id: "appearance.density.comfortable",
            labelKey: "actionDensityComfortable",
            meta: "action:density-comfortable",
            run: (context) => { context.setDensity("comfortable"); context.closePalette(); },
        },
        {
            id: "appearance.density.compact",
            labelKey: "actionDensityCompact",
            meta: "action:density-compact",
            run: (context) => { context.setDensity("compact"); context.closePalette(); },
        },
        {
            id: "navigation.toggle",
            labelKey: "actionToggleNavigation",
            meta: "Ctrl/Cmd+B",
            run: (context) => { context.toggleNavigation(); context.closePalette(); },
        },
        {
            id: "help.commands",
            labelKey: "actionHelp",
            meta: "action:help",
            run: (context) => context.announce(context.t("commandHelp")),
        },
    ];
}
