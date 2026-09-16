import { disposable, DisposableStore, type Disposable } from "./lifecycle.js";
import type { RouteDescriptor } from "./routes.js";

export interface CommandContext {
  t(key: string): string;
  closePalette(): void;
  navigate(hash: string): void;
  toggleNavigation(): void;
  setTheme(theme: "light" | "dark" | "auto"): void;
  setDensity(density: "comfortable" | "compact"): void;
  announce(message: string): void;
  capabilities: Readonly<{ admin: boolean; dataEntry: boolean }>;
}

export interface CommandDescriptor {
  readonly id: string;
  readonly labelKey: string;
  readonly meta: string;
  readonly when?: (context: CommandContext) => boolean;
  readonly run: (context: CommandContext) => void;
}

export interface ResolvedCommand extends CommandDescriptor {
  readonly label: string;
}

export class CommandRegistry {
  private readonly commands = new Map<string, CommandDescriptor>();

  register(command: CommandDescriptor): Disposable {
    if (this.commands.has(command.id)) throw new Error(`Duplicate command: ${command.id}`);
    this.commands.set(command.id, command);
    return disposable(() => this.commands.delete(command.id));
  }

  registerAll(commands: readonly CommandDescriptor[]): Disposable {
    const scope = new DisposableStore();
    commands.forEach((command) => scope.add(this.register(command)));
    return scope;
  }

  list(context: CommandContext, query = ""): ResolvedCommand[] {
    const normalized = query.trim().toLocaleLowerCase();
    return [...this.commands.values()]
      .filter((command) => command.when?.(context) ?? true)
      .map((command) => ({ ...command, label: context.t(command.labelKey) }))
      .filter((command) => !normalized || `${command.label} ${command.meta}`.toLocaleLowerCase().includes(normalized));
  }

  execute(id: string, context: CommandContext): boolean {
    const command = this.commands.get(id);
    if (!command || !(command.when?.(context) ?? true)) return false;
    command.run(context);
    return true;
  }
}

export function createWorkbenchCommands(routes: readonly RouteDescriptor[]): CommandDescriptor[] {
  const navigation = routes
    .filter((route) => route.menu)
    .map((route): CommandDescriptor => ({
      id: `navigate.${route.name}`,
      labelKey: route.titleKey,
      meta: route.path,
      run: (context) => {
        context.closePalette();
        context.navigate(route.path);
      },
      when: "capability" in route
        ? (context) => context.capabilities[route.capability]
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
