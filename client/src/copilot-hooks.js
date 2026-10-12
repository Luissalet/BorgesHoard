// React glue for copilot-context.js: pages say how to reload themselves and which form snapshot they can give.
import { useEffect, useRef } from "react";
import { addRefreshHandler, setViewState } from "./copilot-context.js";

// Calls the latest `reload` when the assistant finishes a turn. `reload` must update state in place (no remount, no reset of forms).
export function useCopilotRefresh(reload) {
  const latest = useRef(reload);
  latest.current = reload;
  useEffect(() => addRefreshHandler(() => latest.current?.()), []);
}

// Answers the `borges:context-request` event with `snapshot()` (a { kind, object_id, dirty, fields } object) while mounted.
export function useContextProvider(snapshot) {
  const latest = useRef(snapshot);
  latest.current = snapshot;
  useEffect(() => {
    const onRequest = (event) => {
      try {
        const value = latest.current?.();
        if (value) event.detail?.provide?.(value);
      } catch { /* a form that cannot describe itself is simply left out */ }
    };
    window.addEventListener("borges:context-request", onRequest);
    return () => window.removeEventListener("borges:context-request", onRequest);
  }, []);
}

// Publishes a piece of view state (search filters, open passage...) for context() and clears it when the view goes away.
export function useViewState(key, value) {
  const serialized = JSON.stringify(value ?? null);
  useEffect(() => {
    setViewState(key, JSON.parse(serialized));
  }, [key, serialized]);
  useEffect(() => () => setViewState(key, null), [key]);
}
