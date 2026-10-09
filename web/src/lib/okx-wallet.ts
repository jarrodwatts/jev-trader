export interface Eip1193RequestArguments {
  method: string;
  params?: readonly unknown[] | Record<string, unknown>;
}

export interface Eip1193Provider {
  request(args: Eip1193RequestArguments): Promise<unknown>;
  on(event: string, listener: (...args: unknown[]) => void): unknown;
  removeListener(event: string, listener: (...args: unknown[]) => void): unknown;
}

export interface OkxDiscoverySurface extends EventTarget {
  okxwallet?: unknown;
}

export type WalletStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | "wrong-network"
  | "reading"
  | "error";

export interface WalletBalances {
  mon: string;
  usdc: string;
  block: string;
}

export interface WalletSnapshot {
  status: WalletStatus;
  account: string | null;
  chainId: string | null;
  balances: WalletBalances | null;
  error: string | null;
}

export interface WalletSession {
  getSnapshot(): WalletSnapshot;
  subscribe(listener: (snapshot: WalletSnapshot) => void): () => void;
  connect(): Promise<void>;
  refresh(): Promise<void>;
  switchToMonad(): Promise<void>;
  disconnect(): void;
  dispose(): void;
}

export interface WalletSessionOptions {
  timeoutMs?: number;
}

const MONAD_CHAIN_ID = "0x8f";
const USDC_CONTRACT = "0x754704Bc059F8C67012fEd69BC8A327a5aafb603";

function isProvider(value: unknown): value is Eip1193Provider {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<Eip1193Provider>;
  return (
    typeof candidate.request === "function" &&
    typeof candidate.on === "function" &&
    typeof candidate.removeListener === "function"
  );
}

function parseAccount(value: unknown): string | null {
  if (typeof value !== "string" || !/^0x[0-9a-fA-F]{40}$/.test(value)) return null;
  return value;
}

function parseAccounts(value: unknown): { account: string | null; invalid: boolean } {
  if (!Array.isArray(value)) return { account: null, invalid: true };
  if (value.length === 0) return { account: null, invalid: false };
  const accounts = value.map(parseAccount);
  if (accounts.some((account) => account === null)) return { account: null, invalid: true };
  return { account: accounts[0], invalid: false };
}

function parseChainId(value: unknown): string | null {
  if (typeof value !== "string" || !/^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$/.test(value)) return null;
  try {
    return `0x${BigInt(value).toString(16)}`;
  } catch {
    return null;
  }
}

function parseQuantity(value: unknown): { raw: string; integer: bigint } | null {
  if (typeof value !== "string" || !/^0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)$/.test(value)) return null;
  try {
    return { raw: value, integer: BigInt(value) };
  } catch {
    return null;
  }
}

function parseTokenBalance(value: unknown): bigint | null {
  if (typeof value !== "string" || !/^0x[0-9a-fA-F]{64}$/.test(value)) return null;
  try {
    return BigInt(value);
  } catch {
    return null;
  }
}

function formatUnits(value: bigint, decimals: number): string {
  const scale = BigInt(10) ** BigInt(decimals);
  const whole = value / scale;
  const fraction = (value % scale).toString().padStart(decimals, "0").replace(/0+$/, "");
  return fraction ? `${whole.toString()}.${fraction}` : whole.toString();
}

function errorCode(error: unknown): number | null {
  if (!error || typeof error !== "object") return null;
  const code = (error as { code?: unknown }).code;
  return typeof code === "number" ? code : null;
}

function safeError(error: unknown, action: "connect" | "read" | "switch"): string {
  const code = errorCode(error);
  if (action === "connect" && code === 4001) return "A conexão foi recusada no OKX.";
  if (action === "switch" && code === 4001) {
    return "A troca de rede foi recusada. Selecione Monad manualmente no OKX e atualize.";
  }
  if (action === "switch" && code === 4902) {
    return "Monad não está disponível nesta carteira. Selecione Monad manualmente no OKX e atualize.";
  }
  if (error instanceof WalletTimeoutError) return "A carteira não respondeu a tempo. Tente atualizar.";
  if (action === "connect") return "Não foi possível conectar. Verifique a extensão OKX e tente novamente.";
  if (action === "switch") return "Não foi possível trocar de rede. Selecione Monad manualmente no OKX e atualize.";
  return "Não foi possível ler os dados da carteira. Verifique a extensão OKX e tente novamente.";
}

class WalletTimeoutError extends Error {}

function requestWithTimeout(
  provider: Eip1193Provider,
  args: Eip1193RequestArguments,
  timeoutMs: number,
): Promise<unknown> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new WalletTimeoutError());
    }, timeoutMs);
    Promise.resolve()
      .then(() => provider.request(args))
      .then(
        (value) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          resolve(value);
        },
        (error: unknown) => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          reject(error);
        },
      );
  });
}

function stateForAccount(account: string | null, chainId: string | null): WalletStatus {
  if (!account) return "disconnected";
  if (!chainId) return "error";
  if (chainId !== MONAD_CHAIN_ID) return "wrong-network";
  return "connected";
}

export function createWalletSession(
  provider: Eip1193Provider,
  options: WalletSessionOptions = {},
): WalletSession {
  const timeoutMs = Math.max(1, options.timeoutMs ?? 8_000);
  const listeners = new Set<(snapshot: WalletSnapshot) => void>();
  let revision = 0;
  let disposed = false;
  let locallyDisconnected = false;
  let activeSwitch: {
    account: string;
    revision: number;
    chainChanged: boolean;
    cancelled: boolean;
  } | null = null;
  let snapshot: WalletSnapshot = {
    status: "disconnected",
    account: null,
    chainId: null,
    balances: null,
    error: null,
  };

  const publish = (next: WalletSnapshot) => {
    if (disposed) return;
    snapshot = next;
    for (const listener of listeners) listener(snapshot);
  };

  const invalidateWith = (next: Omit<WalletSnapshot, "balances">) => {
    revision++;
    publish({ ...next, balances: null });
  };

  const handleAccountsChanged = (...args: unknown[]) => {
    if (disposed || locallyDisconnected) return;
    if (activeSwitch) activeSwitch.cancelled = true;
    const parsed = parseAccounts(args[0]);
    invalidateWith({
      status: parsed.invalid ? "error" : stateForAccount(parsed.account, snapshot.chainId),
      account: parsed.account,
      chainId: snapshot.chainId,
      error: parsed.invalid
        ? "A carteira retornou um endereço inválido. Reconecte no OKX."
        : parsed.account
          ? snapshot.chainId
            ? null
            : "A rede ainda não foi confirmada. Atualize para consultar a carteira."
          : "Nenhuma conta está autorizada. Conecte a carteira para continuar.",
    });
  };

  const handleChainChanged = (...args: unknown[]) => {
    if (disposed || locallyDisconnected) return;
    const chainId = parseChainId(args[0]);
    if (activeSwitch) {
      if (chainId && snapshot.account === activeSwitch.account) activeSwitch.chainChanged = true;
      else activeSwitch.cancelled = true;
    }
    invalidateWith({
      status: chainId ? stateForAccount(snapshot.account, chainId) : "error",
      account: snapshot.account,
      chainId,
      error: chainId
        ? chainId === MONAD_CHAIN_ID || !snapshot.account
          ? null
          : "Troque para Monad para consultar MON e USDC."
        : "A carteira retornou um identificador de rede inválido.",
    });
  };

  const handleDisconnect = () => {
    if (disposed || locallyDisconnected) return;
    if (activeSwitch) activeSwitch.cancelled = true;
    invalidateWith({
      status: "disconnected",
      account: null,
      chainId: null,
      error: "A carteira OKX foi desconectada. Reconecte para continuar.",
    });
  };

  provider.on("accountsChanged", handleAccountsChanged);
  provider.on("chainChanged", handleChainChanged);
  provider.on("disconnect", handleDisconnect);

  const requestUntil = (
    method: string,
    params: Eip1193RequestArguments["params"],
    deadline: number,
  ) => {
    const remaining = deadline - Date.now();
    if (remaining <= 0) return Promise.reject(new WalletTimeoutError());
    return requestWithTimeout(
      provider,
      { method, ...(params === undefined ? {} : { params }) },
      remaining,
    );
  };

  const request = (method: string, params?: Eip1193RequestArguments["params"]) =>
    requestUntil(method, params, Date.now() + timeoutMs);

  const acceptAccountAndChain = (accountValue: unknown, chainValue: unknown): boolean => {
    const parsedAccount = parseAccounts(accountValue);
    const chainId = parseChainId(chainValue);
    if (parsedAccount.invalid || !chainId) {
      publish({
        status: "error",
        account: null,
        chainId,
        balances: null,
        error: parsedAccount.invalid
          ? "A carteira retornou um endereço inválido. Reconecte no OKX."
          : "A carteira retornou um identificador de rede inválido.",
      });
      return false;
    }
    if (!parsedAccount.account) {
      publish({
        status: "disconnected",
        account: null,
        chainId,
        balances: null,
        error: "Nenhuma conta está autorizada. Conecte a carteira para continuar.",
      });
      return false;
    }
    if (chainId !== MONAD_CHAIN_ID) {
      publish({
        status: "wrong-network",
        account: parsedAccount.account,
        chainId,
        balances: null,
        error: "Troque para Monad para consultar MON e USDC.",
      });
      return false;
    }
    publish({
      status: "connected",
      account: parsedAccount.account,
      chainId,
      balances: null,
      error: null,
    });
    return true;
  };

  const readBalances = async (operation: number, account: string): Promise<void> => {
    const deadline = Date.now() + timeoutMs;
    publish({
      status: "reading",
      account,
      chainId: MONAD_CHAIN_ID,
      balances: null,
      error: null,
    });
    try {
      const rawBlock = await requestUntil("eth_blockNumber", undefined, deadline);
      const block = parseQuantity(rawBlock);
      if (!block) throw new Error("invalid block response");
      if (disposed || operation !== revision) return;

      const callData = `0x70a08231${account.slice(2).toLowerCase().padStart(64, "0")}`;
      const [rawMon, rawUsdc] = await Promise.all([
        requestUntil("eth_getBalance", [account, block.raw], deadline),
        requestUntil("eth_call", [{ to: USDC_CONTRACT, data: callData }, block.raw], deadline),
      ]);
      const mon = parseQuantity(rawMon);
      const usdc = parseTokenBalance(rawUsdc);
      if (!mon || usdc === null) throw new Error("invalid balance response");
      if (disposed || operation !== revision) return;
      publish({
        status: "connected",
        account,
        chainId: MONAD_CHAIN_ID,
        balances: {
          mon: formatUnits(mon.integer, 18),
          usdc: formatUnits(usdc, 6),
          block: block.raw,
        },
        error: null,
      });
    } catch (error) {
      if (disposed || operation !== revision) return;
      publish({
        status: "error",
        account,
        chainId: MONAD_CHAIN_ID,
        balances: null,
        error: safeError(error, "read"),
      });
    }
  };

  const connect = async (): Promise<void> => {
    if (disposed) return;
    locallyDisconnected = false;
    if (activeSwitch) activeSwitch.cancelled = true;
    const operation = ++revision;
    publish({ status: "connecting", account: null, chainId: null, balances: null, error: null });
    try {
      const accounts = await request("eth_requestAccounts");
      if (disposed || operation !== revision) return;
      const parsed = parseAccounts(accounts);
      if (parsed.invalid || !parsed.account) {
        publish({
          status: parsed.invalid ? "error" : "disconnected",
          account: null,
          chainId: null,
          balances: null,
          error: parsed.invalid
            ? "A carteira retornou um endereço inválido. Reconecte no OKX."
            : "Nenhuma conta foi selecionada no OKX.",
        });
        return;
      }
      const chainValue = await request("eth_chainId");
      if (disposed || operation !== revision) return;
      if (acceptAccountAndChain(accounts, chainValue) && snapshot.account) {
        await readBalances(operation, snapshot.account);
      }
    } catch (error) {
      if (disposed || operation !== revision) return;
      publish({
        status: "error",
        account: null,
        chainId: null,
        balances: null,
        error: safeError(error, "connect"),
      });
    }
  };

  const refresh = async (): Promise<void> => {
    if (disposed || locallyDisconnected) return;
    if (activeSwitch) activeSwitch.cancelled = true;
    const operation = ++revision;
    publish({ ...snapshot, status: "reading", balances: null, error: null });
    try {
      const accounts = await request("eth_accounts");
      if (disposed || operation !== revision) return;
      const chainValue = await request("eth_chainId");
      if (disposed || operation !== revision) return;
      if (acceptAccountAndChain(accounts, chainValue) && snapshot.account) {
        await readBalances(operation, snapshot.account);
      }
    } catch (error) {
      if (disposed || operation !== revision) return;
      publish({ ...snapshot, status: "error", balances: null, error: safeError(error, "read") });
    }
  };

  const switchToMonad = async (): Promise<void> => {
    if (disposed) return;
    if (!snapshot.account) {
      publish({ ...snapshot, status: "disconnected", balances: null, error: "Conecte uma conta primeiro." });
      return;
    }
    if (activeSwitch) activeSwitch.cancelled = true;
    const attempt = {
      account: snapshot.account,
      revision: ++revision,
      chainChanged: false,
      cancelled: false,
    };
    activeSwitch = attempt;
    publish({ ...snapshot, status: "reading", balances: null, error: null });
    try {
      await request("wallet_switchEthereumChain", [{ chainId: MONAD_CHAIN_ID }]);
      if (disposed || attempt.cancelled) return;
      if (attempt.revision !== revision) {
        if (!attempt.chainChanged || snapshot.account !== attempt.account) return;
        attempt.revision = ++revision;
      }
      const chainId = parseChainId(await request("eth_chainId"));
      if (disposed || attempt.cancelled || attempt.revision !== revision) return;
      if (chainId !== MONAD_CHAIN_ID) {
        publish({
          ...snapshot,
          status: chainId ? "wrong-network" : "error",
          chainId,
          balances: null,
          error: chainId
            ? "Troque para Monad para consultar MON e USDC."
            : "A carteira retornou um identificador de rede inválido.",
        });
        return;
      }
      publish({ ...snapshot, status: "connected", chainId, balances: null, error: null });
      await readBalances(attempt.revision, attempt.account);
    } catch (error) {
      if (disposed || attempt.cancelled || attempt.revision !== revision) return;
      publish({
        ...snapshot,
        status: snapshot.chainId
          ? stateForAccount(snapshot.account, snapshot.chainId)
          : "error",
        balances: null,
        error: safeError(error, "switch"),
      });
    } finally {
      if (activeSwitch === attempt) activeSwitch = null;
    }
  };

  return {
    getSnapshot: () => snapshot,
    subscribe(listener) {
      if (disposed) return () => {};
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    connect,
    refresh,
    switchToMonad,
    disconnect() {
      if (disposed) return;
      locallyDisconnected = true;
      if (activeSwitch) activeSwitch.cancelled = true;
      invalidateWith({
        status: "disconnected",
        account: null,
        chainId: null,
        error: "Sessão encerrada neste painel. As permissões do OKX não foram alteradas.",
      });
    },
    dispose() {
      if (disposed) return;
      if (activeSwitch) activeSwitch.cancelled = true;
      revision++;
      disposed = true;
      provider.removeListener("accountsChanged", handleAccountsChanged);
      provider.removeListener("chainChanged", handleChainChanged);
      provider.removeListener("disconnect", handleDisconnect);
      snapshot = {
        status: "disconnected",
        account: null,
        chainId: null,
        balances: null,
        error: null,
      };
      listeners.clear();
    },
  };
}

interface Eip6963Announcement {
  provider?: unknown;
}

/** Discover only the provider exposed directly on `okxwallet` on this surface. */
export function discoverOkxProvider(
  surface: OkxDiscoverySurface,
  onProvider: (provider: Eip1193Provider) => void,
): () => void {
  let active = true;
  let reported: Eip1193Provider | null = null;

  const reportAnchor = () => {
    const anchor = isProvider(surface.okxwallet) ? surface.okxwallet : null;
    if (active && anchor && anchor !== reported) {
      reported = anchor;
      onProvider(anchor);
    }
  };

  const handleAnnouncement: EventListener = (event) => {
    const detail = (event as CustomEvent<Eip6963Announcement>).detail;
    if (!detail || !isProvider(detail.provider)) return;
    const anchor = isProvider(surface.okxwallet) ? surface.okxwallet : null;
    if (anchor && detail.provider === anchor) reportAnchor();
  };

  surface.addEventListener("eip6963:announceProvider", handleAnnouncement);
  reportAnchor();
  surface.dispatchEvent(new Event("eip6963:requestProvider"));

  return () => {
    if (!active) return;
    active = false;
    surface.removeEventListener("eip6963:announceProvider", handleAnnouncement);
  };
}
