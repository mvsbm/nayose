export const ROUTES = [
    { name: "search", nav: "search", path: "#/search", titleKey: "search", workspace: "match" },
    { name: "review", nav: "review", path: "#/review", titleKey: "review", workspace: "match" },
    { name: "confusion", nav: "confusion", path: "#/confusion", titleKey: "confusion", workspace: "match" },
    { name: "metrics", nav: "metrics", path: "#/metrics", titleKey: "metrics", workspace: "match" },
    { name: "entities", nav: "entities", path: "#/entities", titleKey: "entityExplorer", workspace: "explore" },
    { name: "entity", nav: "entities", path: "#/entity", titleKey: "entityExplorer", workspace: "explore" },
    { name: "compare", nav: "review", path: "#/compare", titleKey: "compare", workspace: "match" },
    { name: "compare-runs", nav: "compare-runs", path: "#/compare-runs", titleKey: "compareRuns", workspace: "match" },
    { name: "admin", nav: "admin", path: "#/admin", titleKey: "dataTrain", workspace: "manage" },
    { name: "data-entry", nav: "data-entry", path: "#/data-entry", titleKey: "dataEntry", workspace: "manage" },
];
const routeByName = new Map(ROUTES.map((route) => [route.name, route]));
export function isRouteName(value) {
    return routeByName.has(value);
}
export function routeDescriptor(value) {
    return routeByName.get(value) ?? ROUTES[0];
}
export function routeHash(name, args = []) {
    const route = routeDescriptor(name);
    return [route.path, ...args].join("/");
}
