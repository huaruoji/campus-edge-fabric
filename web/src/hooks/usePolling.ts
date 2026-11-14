import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { isAbortError } from "../api";

export interface PollState<T> {
  data: T | null;
  error: unknown | null;
  loading: boolean;
  updatedAt: number | null;
}

export interface PollResult<T> extends PollState<T> {
  refresh: () => void;
}

function initialState<T>(enabled: boolean): PollState<T> {
  return { data: null, error: null, loading: enabled, updatedAt: null };
}

/**
 * 轮询一个异步数据源。
 * - enabled 为 false 时不发请求（例如尚未填写 Token）；
 * - 请求失败保留上一次成功数据，同时把 error 暴露给界面；
 * - refresh() 立即重跑（会重建轮询循环）；
 * - deps 变化（例如 api 客户端或 jobId 变化）会清除旧数据、取消旧请求并重新开始。
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  enabled: boolean,
  deps: readonly unknown[],
): PollResult<T> {
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const source = useMemo(() => ({}), [enabled, ...deps]);
  const [snapshot, setSnapshot] = useState(() => ({ source, state: initialState<T>(enabled) }));
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: number | undefined;
    const controller = new AbortController();

    setSnapshot((prev) => prev.source === source
      ? { source, state: { ...prev.state, loading: prev.state.data === null } }
      : { source, state: initialState<T>(enabled) },
    );

    const tick = async (): Promise<void> => {
      try {
        const data = await fetcherRef.current(controller.signal);
        if (cancelled) return;
        setSnapshot({ source, state: { data, error: null, loading: false, updatedAt: Date.now() } });
      } catch (error) {
        if (cancelled || isAbortError(error)) return;
        setSnapshot((prev) => ({ source, state: { ...prev.state, error, loading: false } }));
      } finally {
        if (!cancelled) {
          timer = window.setTimeout(() => {
            void tick();
          }, intervalMs);
        }
      }
    };

    void tick();

    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [enabled, intervalMs, nonce, source]);

  const refresh = useCallback(() => {
    setNonce((value) => value + 1);
  }, []);

  const state = snapshot.source === source ? snapshot.state : initialState<T>(enabled);
  return { ...state, refresh };
}
