import { presentError } from "./api-error.js";
import { disposable } from "./lifecycle.js";
export function createDecisionCoordinator(store, port, owner) {
    const storageKey = (runId) => `nayose.checked.${encodeURIComponent(runId)}`;
    const localDecisions = (runId) => {
        const scoped = store.read(storageKey(runId));
        const legacy = scoped === null ? store.read("nayose.checked") : null;
        try {
            const parsed = JSON.parse(scoped ?? legacy ?? "[]");
            const values = Array.isArray(parsed) ? parsed.filter((key) => typeof key === "string") : [];
            return new Set(values.filter(key => key.startsWith(`${runId}|`)));
        }
        catch {
            return new Set();
        }
    };
    const persistLocal = (runId) => {
        store.write(storageKey(runId), JSON.stringify([...store.checked]));
    };
    let loadRequest = 0;
    let epoch = 0;
    let save = Promise.resolve();
    let saveTimer;
    let loadController;
    let saveController;
    owner.add(disposable(() => {
        loadController?.abort();
        saveController?.abort();
        owner.clearTimeout(saveTimer);
    }));
    const loadForRun = async (runId) => {
        const request = ++loadRequest;
        epoch += 1;
        owner.clearTimeout(saveTimer);
        loadController?.abort();
        saveController?.abort();
        loadController = new AbortController();
        const requestController = loadController;
        store.checked = localDecisions(runId);
        store.setDecisionRevision(0);
        store.setDecisionConflict(false);
        store.setDecisionSaveError("");
        store.setDecisionLoadError("");
        try {
            const remote = await port.loadDecisions(runId, requestController.signal);
            if (owner.isDisposed || request !== loadRequest || store.run !== runId)
                return;
            store.setDecisionSnapshot(remote, false);
            store.setDecisionLoadError("");
            persistLocal(runId);
        }
        catch (error) {
            if (owner.isDisposed || request !== loadRequest || store.run !== runId || requestController.signal.aborted)
                return;
            store.setDecisionLoadError(presentError(error, store.t.bind(store)));
        }
        finally {
            if (loadController === requestController)
                loadController = undefined;
        }
    };
    const persist = () => {
        const runId = store.run;
        persistLocal(runId);
        const currentEpoch = ++epoch;
        owner.clearTimeout(saveTimer);
        saveTimer = owner.addTimeout(() => {
            const now = new Date().toISOString();
            const items = [...store.checked]
                .filter(key => key.startsWith(`${runId}|`))
                .map(key => {
                const parts = key.split("|");
                return { left: parts[1] ?? "", right: parts[2] ?? "", ts: now, reviewer: store.reviewer };
            });
            if (store.decisionConflict || currentEpoch !== epoch)
                return;
            saveController?.abort();
            saveController = new AbortController();
            const currentController = saveController;
            save = save
                .catch(() => undefined)
                .then(() => {
                if (currentEpoch !== epoch)
                    return;
                return port.saveDecisions(items, store.decisionRevision, runId, currentController.signal).then(revision => {
                    if (!owner.isDisposed && store.run === runId && currentEpoch === epoch) {
                        store.setDecisionRevision(revision);
                        store.setDecisionSaveError("");
                    }
                });
            })
                .catch(error => {
                if (owner.isDisposed || store.run !== runId || currentEpoch !== epoch)
                    return;
                if (error.message === "decision_conflict")
                    store.setDecisionConflict(true);
                else
                    store.setDecisionSaveError(presentError(error, store.t.bind(store)));
            })
                .finally(() => {
                if (saveController === currentController)
                    saveController = undefined;
            });
        }, 500);
    };
    owner.add(disposable(store.on("checked", persist)));
    return { loadForRun };
}
