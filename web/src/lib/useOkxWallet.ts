"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createWalletSession,
  discoverOkxProvider,
  type OkxDiscoverySurface,
  type WalletSession,
  type WalletSnapshot,
} from "./okx-wallet";

const initialSnapshot: WalletSnapshot = {
  status: "disconnected",
  account: null,
  chainId: null,
  balances: null,
  error: null,
};

export interface UseOkxWalletResult {
  available: boolean;
  ready: boolean;
  snapshot: WalletSnapshot;
  connect(): Promise<void>;
  refresh(): Promise<void>;
  switchToMonad(): Promise<void>;
  disconnect(): void;
}

/** Browser-only adapter. Discovery never requests account access. */
export function useOkxWallet(): UseOkxWalletResult {
  const sessionRef = useRef<WalletSession | null>(null);
  const [available, setAvailable] = useState(false);
  const [ready, setReady] = useState(false);
  const [snapshot, setSnapshot] = useState(initialSnapshot);

  useEffect(() => {
    if (typeof window === "undefined") return;

    let currentSession: WalletSession | null = null;
    let unsubscribe: (() => void) | null = null;
    const stopDiscovery = discoverOkxProvider(
      window as unknown as OkxDiscoverySurface,
      (provider) => {
        if (currentSession) {
          unsubscribe?.();
          currentSession.dispose();
        }
        currentSession = createWalletSession(provider);
        sessionRef.current = currentSession;
        unsubscribe = currentSession.subscribe(setSnapshot);
        setSnapshot(currentSession.getSnapshot());
        setAvailable(true);
      },
    );
    setReady(true);

    return () => {
      stopDiscovery();
      unsubscribe?.();
      currentSession?.dispose();
      if (sessionRef.current === currentSession) sessionRef.current = null;
    };
  }, []);

  const connect = useCallback(() => sessionRef.current?.connect() ?? Promise.resolve(), []);
  const refresh = useCallback(() => sessionRef.current?.refresh() ?? Promise.resolve(), []);
  const switchToMonad = useCallback(
    () => sessionRef.current?.switchToMonad() ?? Promise.resolve(),
    [],
  );
  const disconnect = useCallback(() => sessionRef.current?.disconnect(), []);

  return { available, ready, snapshot, connect, refresh, switchToMonad, disconnect };
}
