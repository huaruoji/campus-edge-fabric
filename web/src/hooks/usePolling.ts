import { useCallback, useEffect, useRef, useState } from "react";
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

/**
 * 轮询一个异步数据源。
 * - enabled 为 false 时不发请求（例如尚未填写 Token）；
 * - 请求失败保留上一次成功数据，同时把 error 暴露给界面；
 * - refresh() 立即重跑（会重建轮询循环）；
 * - deps 变化（例如 api 客户端或 jobId 变化）会取消旧请求并重新开始。
 */
export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs: number,
  enabled: boolean,
  deps: readonly unknown[],
): PollResult<T> {
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const [state, setState] = useState<PollState<T>>({
    data: null,
    error: null,
    loading: enabled,
    updatedAt: null,
  });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let timer: number | undefined;
    const controller = new AbortController();

    setState((prev) => ({ ...prev, loading: prev.data === null }));

    const tick = async (): Promise<void> => {
      try {
        const data = await fetcherRef.current(controller.signal);
        if (cancelled) return;
        setState({ data, error: null, loading: false, updatedAt: Date.now() });
      } catch (error) {
        if (cancelled || isAbortError(error)) return;
        setState((prev) => ({ ...prev, error, loading: false }));
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
    // deps 由调用方显式提供，用于在 api/jobId 变化时重启轮询。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, intervalMs, nonce, ...deps]);

  const refresh = useCallback(() => {
    setNonce((value) => value + 1);
  }, []);

  return { ...state, refresh };
}
