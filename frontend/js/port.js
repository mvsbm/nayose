// Ports (hexagonal): the frontend depends on these interfaces, never on
// fetch/SQL/localStorage directly. Swap WASM vs API by providing a different
// QueryPort at the composition root.
export {};
