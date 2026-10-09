import { describe, expect, it } from "bun:test";
import {
  createWalletSession,
  discoverOkxProvider,
  type Eip1193Provider,
  type Eip1193RequestArguments,
} from "./okx-wallet";

class DiscoverySurface extends EventTarget {
  okxwallet: unknown;
}

function provider(): Eip1193Provider & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    async request({ method }) {
      calls.push(method);
      return [];
    },
    on() {},
    removeListener() {},
  };
}

class RequestProvider implements Eip1193Provider {
  readonly requests: Eip1193RequestArguments[] = [];
  private readonly handlers = new Map<string, Set<(...args: unknown[]) => void>>();

  constructor(private readonly respond: (method: string) => unknown | Promise<unknown>) {}

  async request(args: Eip1193RequestArguments): Promise<unknown> {
    this.requests.push(args);
    return this.respond(args.method);
  }

  on(event: string, listener: (...args: unknown[]) => void): void {
    const listeners = this.handlers.get(event) ?? new Set();
    listeners.add(listener);
    this.handlers.set(event, listeners);
  }

  removeListener(event: string, listener: (...args: unknown[]) => void): void {
    this.handlers.get(event)?.delete(listener);
  }

  emit(event: string, ...args: unknown[]): void {
    for (const listener of this.handlers.get(event) ?? []) listener(...args);
  }

  listenerCount(event: string): number {
    return this.handlers.get(event)?.size ?? 0;
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

async function waitForRequest(wallet: RequestProvider, method: string): Promise<void> {
  for (let attempt = 0; attempt < 30; attempt++) {
    if (wallet.requests.some((request) => request.method === method)) return;
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
  throw new Error(`Timed out waiting for ${method} in the test provider.`);
}

const validAccount = "0x1111111111111111111111111111111111111111";
const usdcContract = "0x754704Bc059F8C67012fEd69BC8A327a5aafb603";

describe("OKX provider discovery", () => {
  it("uses only the anchored provider reference and never asks for accounts", () => {
    const anchor = provider();
    const impostor = provider();
    const surface = new DiscoverySurface();
    surface.okxwallet = anchor;
    const found: Eip1193Provider[] = [];

    const cleanup = discoverOkxProvider(surface, (wallet) => found.push(wallet));
    surface.dispatchEvent(
      new CustomEvent("eip6963:announceProvider", {
        detail: {
          info: { name: "OKX Wallet", rdns: "com.okx.wallet" },
          provider: impostor,
        },
      }),
    );

    expect(found).toEqual([anchor]);
    expect(anchor.calls).toEqual([]);
    expect(impostor.calls).toEqual([]);
    cleanup();
  });

  it("does not trust a metadata-only EIP-6963 announcement without the anchor", () => {
    const impostor = provider();
    const surface = new DiscoverySurface();
    surface.okxwallet = undefined;
    const found: Eip1193Provider[] = [];

    const cleanup = discoverOkxProvider(surface, (wallet) => found.push(wallet));
    surface.dispatchEvent(
      new CustomEvent("eip6963:announceProvider", {
        detail: {
          info: { name: "OKX Wallet", rdns: "com.okx.wallet" },
          provider: impostor,
        },
      }),
    );

    expect(found).toEqual([]);
    expect(impostor.calls).toEqual([]);
    cleanup();
  });
});

describe("OKX wallet session", () => {
  it("does not claim Monad connection when an account event arrives before the network is known", () => {
    const wallet = new RequestProvider(() => { throw new Error("No RPC expected from an event."); });
    const session = createWalletSession(wallet);

    wallet.emit("accountsChanged", [validAccount]);

    expect(session.getSnapshot()).toMatchObject({
      status: "error",
      account: validAccount,
      chainId: null,
      balances: null,
      error: "A rede ainda não foi confirmada. Atualize para consultar a carteira.",
    });
    expect(wallet.requests).toEqual([]);
    session.dispose();
  });

  it("requests accounts only after explicit connect and blocks the wrong network", async () => {
    const wallet = new RequestProvider((method) => {
      if (method === "eth_requestAccounts") return [validAccount];
      if (method === "eth_chainId") return "0x1";
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const session = createWalletSession(wallet);

    expect(wallet.requests).toEqual([]);
    await session.connect();

    expect(wallet.requests.map(({ method }) => method)).toEqual([
      "eth_requestAccounts",
      "eth_chainId",
    ]);
    expect(session.getSnapshot()).toMatchObject({
      status: "wrong-network",
      account: validAccount,
      chainId: "0x1",
      balances: null,
    });
    session.dispose();
  });

  it("reads exact MON and USDC balances at one block using the documented USDC contract", async () => {
    const wallet = new RequestProvider((method) => {
      if (method === "eth_requestAccounts") return [validAccount];
      if (method === "eth_chainId") return "0x8f";
      if (method === "eth_blockNumber") return "0xabc";
      if (method === "eth_getBalance") return "0x3635c9adc5dea004d2";
      if (method === "eth_call") return `0x${"00".repeat(24)}0000000000bc614e`;
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const session = createWalletSession(wallet);

    await session.connect();

    expect(session.getSnapshot()).toMatchObject({
      status: "connected",
      account: validAccount,
      chainId: "0x8f",
      balances: {
        mon: "1000.000000000000001234",
        usdc: "12.345678",
        block: "0xabc",
      },
    });
    const nativeRead = wallet.requests.find(({ method }) => method === "eth_getBalance");
    const tokenRead = wallet.requests.find(({ method }) => method === "eth_call");
    expect(nativeRead?.params).toEqual([validAccount, "0xabc"]);
    expect(tokenRead?.params).toEqual([
      {
        to: usdcContract,
        data: `0x70a08231${validAccount.slice(2).toLowerCase().padStart(64, "0")}`,
      },
      "0xabc",
    ]);
    expect(wallet.requests.filter(({ method }) => method === "eth_call")).toHaveLength(1);
    session.dispose();
  });

  it("blocks malformed RPC values and turns a stalled balance request into unavailable data", async () => {
    for (const [methodToCorrupt, badValue] of [
      ["eth_getBalance", "0x00"],
      ["eth_call", "0x1234"],
    ] as const) {
      const wallet = new RequestProvider((method) => {
        if (method === "eth_requestAccounts") return [validAccount];
        if (method === "eth_chainId") return "0x8f";
        if (method === "eth_blockNumber") return "0xabc";
        if (method === methodToCorrupt) return badValue;
        if (method === "eth_getBalance") return "0x0";
        if (method === "eth_call") return `0x${"00".repeat(32)}`;
        throw new Error(`Unexpected provider method: ${method}`);
      });
      const session = createWalletSession(wallet);

      await session.connect();

      expect(session.getSnapshot().status).toBe("error");
      expect(session.getSnapshot().balances).toBeNull();
      expect(wallet.requests.filter(({ method }) => method === methodToCorrupt)).toHaveLength(1);
      session.dispose();
    }

    const stalled = new RequestProvider((method) => {
      if (method === "eth_requestAccounts") return [validAccount];
      if (method === "eth_chainId") return "0x8f";
      if (method === "eth_blockNumber") return new Promise(() => {});
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const timedSession = createWalletSession(stalled, { timeoutMs: 10 });

    await timedSession.connect();

    expect(timedSession.getSnapshot().status).toBe("error");
    expect(timedSession.getSnapshot().balances).toBeNull();
    expect(stalled.requests.map(({ method }) => method)).toEqual([
      "eth_requestAccounts",
      "eth_chainId",
      "eth_blockNumber",
    ]);
    timedSession.dispose();
  });

  it("reports empty, malformed, and rejected account authorization without exposing provider errors", async () => {
    for (const [accounts, expectedStatus] of [
      [[], "disconnected"],
      [["not-an-address"], "error"],
    ] as const) {
      const wallet = new RequestProvider((method) => {
        if (method === "eth_requestAccounts") return accounts;
        throw new Error(`Unexpected provider method: ${method}`);
      });
      const session = createWalletSession(wallet);

      await session.connect();

      expect(session.getSnapshot().status).toBe(expectedStatus);
      expect(wallet.requests.map(({ method }) => method)).toEqual(["eth_requestAccounts"]);
      session.dispose();
    }

    const rejected = new RequestProvider((method) => {
      if (method === "eth_requestAccounts") return Promise.reject({
        code: 4001,
        message: "sensitive extension details must not reach the panel",
      });
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const rejectedSession = createWalletSession(rejected);

    await rejectedSession.connect();

    expect(rejectedSession.getSnapshot().status).toBe("error");
    expect(rejectedSession.getSnapshot().error).not.toContain("sensitive extension details");
    expect(rejected.requests.map(({ method }) => method)).toEqual(["eth_requestAccounts"]);
    rejectedSession.dispose();

    const invalidChain = new RequestProvider((method) => {
      if (method === "eth_requestAccounts") return [validAccount];
      if (method === "eth_chainId") return "0x01";
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const invalidChainSession = createWalletSession(invalidChain);

    await invalidChainSession.connect();

    expect(invalidChainSession.getSnapshot()).toMatchObject({
      status: "error",
      chainId: null,
      balances: null,
    });
    expect(invalidChain.requests.map(({ method }) => method)).toEqual([
      "eth_requestAccounts",
      "eth_chainId",
    ]);
    invalidChainSession.dispose();
  });

  it("switches only on explicit request and rereads the confirmed chain before reading balances", async () => {
    let chainId = "0x1";
    const wallet = new RequestProvider((method) => {
      if (method === "eth_requestAccounts") return [validAccount];
      if (method === "eth_chainId") return chainId;
      if (method === "wallet_switchEthereumChain") {
        chainId = "0x8f";
        return null;
      }
      if (method === "eth_blockNumber") return "0xabc";
      if (method === "eth_getBalance") return "0x0";
      if (method === "eth_call") return `0x${"00".repeat(32)}`;
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const session = createWalletSession(wallet);

    await session.connect();

    expect(session.getSnapshot().status).toBe("wrong-network");
    expect(wallet.requests.some(({ method }) => method === "eth_blockNumber")).toBe(false);
    expect(wallet.requests.some(({ method }) => method === "wallet_switchEthereumChain")).toBe(false);

    await session.switchToMonad();

    expect(wallet.requests.find(({ method }) => method === "wallet_switchEthereumChain")?.params).toEqual([
      { chainId: "0x8f" },
    ]);
    expect(wallet.requests.map(({ method }) => method)).toEqual([
      "eth_requestAccounts",
      "eth_chainId",
      "wallet_switchEthereumChain",
      "eth_chainId",
      "eth_blockNumber",
      "eth_getBalance",
      "eth_call",
    ]);
    expect(session.getSnapshot().status).toBe("connected");
    session.dispose();
  });

  it("rereads chain after the explicit switch emits chainChanged before its request resolves", async () => {
    let chainId = "0x1";
    let wallet!: RequestProvider;
    wallet = new RequestProvider((method) => {
      if (method === "eth_requestAccounts") return [validAccount];
      if (method === "eth_chainId") return chainId;
      if (method === "wallet_switchEthereumChain") {
        chainId = "0x8f";
        wallet.emit("chainChanged", chainId);
        return null;
      }
      if (method === "eth_blockNumber") return "0xabc";
      if (method === "eth_getBalance") return "0x0";
      if (method === "eth_call") return `0x${"00".repeat(32)}`;
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const session = createWalletSession(wallet);

    await session.connect();
    await session.switchToMonad();

    expect(wallet.requests.filter(({ method }) => method === "eth_chainId")).toHaveLength(2);
    expect(session.getSnapshot()).toMatchObject({ chainId: "0x8f", status: "connected" });
    session.dispose();
  });

  it("leaves rejected or unavailable network switches for manual selection", async () => {
    for (const code of [4001, 4902]) {
      const wallet = new RequestProvider((method) => {
        if (method === "eth_requestAccounts") return [validAccount];
        if (method === "eth_chainId") return "0x1";
        if (method === "wallet_switchEthereumChain") {
          return Promise.reject({ code, message: "private provider details" });
        }
        throw new Error(`Unexpected provider method: ${method}`);
      });
      const session = createWalletSession(wallet);
      await session.connect();
      await session.switchToMonad();

      expect(session.getSnapshot()).toMatchObject({
        status: "wrong-network",
        balances: null,
      });
      expect(session.getSnapshot().error).toContain("manualmente");
      expect(session.getSnapshot().error).not.toContain("private provider details");
      expect(wallet.requests.some(({ method }) => method === "wallet_addEthereumChain")).toBe(false);
      session.dispose();
    }
  });

  it("refreshes using eth_accounts without prompting for new authorization", async () => {
    const wallet = new RequestProvider((method) => {
      if (method === "eth_accounts") return [validAccount];
      if (method === "eth_chainId") return "0x8f";
      if (method === "eth_blockNumber") return "0xabc";
      if (method === "eth_getBalance") return "0x0";
      if (method === "eth_call") return `0x${"00".repeat(32)}`;
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const session = createWalletSession(wallet);

    await session.refresh();

    expect(wallet.requests.map(({ method }) => method)).toEqual([
      "eth_accounts",
      "eth_chainId",
      "eth_blockNumber",
      "eth_getBalance",
      "eth_call",
    ]);
    expect(session.getSnapshot().status).toBe("connected");
    session.dispose();
  });

  it("invalidates delayed balance reads after wallet events and local disconnect", async () => {
    for (const event of ["accountsChanged", "chainChanged", "disconnect", "localDisconnect", "dispose"] as const) {
      const tokenRead = deferred<unknown>();
      const wallet = new RequestProvider((method) => {
        if (method === "eth_requestAccounts") return [validAccount];
        if (method === "eth_chainId") return "0x8f";
        if (method === "eth_blockNumber") return "0xabc";
        if (method === "eth_getBalance") return "0x0";
        if (method === "eth_call") return tokenRead.promise;
        throw new Error(`Unexpected provider method: ${method}`);
      });
      const session = createWalletSession(wallet);
      const connecting = session.connect();
      await waitForRequest(wallet, "eth_call");

      if (event === "accountsChanged") wallet.emit(event, ["0x2222222222222222222222222222222222222222"]);
      else if (event === "chainChanged") wallet.emit(event, "0x1");
      else if (event === "disconnect") wallet.emit(event, { code: 4900 });
      else if (event === "dispose") session.dispose();
      else session.disconnect();
      tokenRead.resolve(`0x${"00".repeat(32)}`);
      await connecting;

      expect(session.getSnapshot().balances).toBeNull();
      if (event === "accountsChanged") {
        expect(session.getSnapshot()).toMatchObject({
          account: "0x2222222222222222222222222222222222222222",
          status: "connected",
        });
      } else if (event === "chainChanged") {
        expect(session.getSnapshot()).toMatchObject({ chainId: "0x1", status: "wrong-network" });
      } else {
        expect(session.getSnapshot()).toMatchObject({ account: null, status: "disconnected" });
      }
      session.dispose();
    }
  });

  it("keeps local disconnect until explicit reconnect despite provider events", async () => {
    const wallet = new RequestProvider((method) => {
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [validAccount];
      if (method === "eth_chainId") return "0x8f";
      if (method === "eth_blockNumber") return "0xabc";
      if (method === "eth_getBalance") return "0x0";
      if (method === "eth_call") return `0x${"00".repeat(32)}`;
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const session = createWalletSession(wallet);
    await session.connect();
    session.disconnect();
    const disconnected = session.getSnapshot();
    const requestsBeforeEvents = wallet.requests.length;

    wallet.emit("accountsChanged", [validAccount]);
    wallet.emit("chainChanged", "0x8f");
    wallet.emit("disconnect", { code: 4900 });
    wallet.emit("chainChanged", "0x1");
    wallet.emit("accountsChanged", ["0x2222222222222222222222222222222222222222"]);
    await session.refresh();

    expect(session.getSnapshot()).toEqual(disconnected);
    expect(wallet.requests).toHaveLength(requestsBeforeEvents);
    await session.connect();
    expect(wallet.requests.filter(({ method }) => method === "eth_requestAccounts")).toHaveLength(2);
    expect(session.getSnapshot()).toMatchObject({ status: "connected", account: validAccount, chainId: "0x8f" });
    wallet.emit("chainChanged", "0x1");
    expect(session.getSnapshot()).toMatchObject({ status: "wrong-network", balances: null });
    session.dispose();
  });

  it("does not let a previous connection overwrite a newer connection or survive disposal", async () => {
    const staleTokenRead = deferred<unknown>();
    let tokenCalls = 0;
    const wallet = new RequestProvider((method) => {
      if (method === "eth_requestAccounts") return [validAccount];
      if (method === "eth_chainId") return "0x8f";
      if (method === "eth_blockNumber") return tokenCalls === 0 ? "0xabc" : "0xabd";
      if (method === "eth_getBalance") return "0x0";
      if (method === "eth_call") {
        tokenCalls++;
        return tokenCalls === 1 ? staleTokenRead.promise : `0x${"00".repeat(32)}`;
      }
      throw new Error(`Unexpected provider method: ${method}`);
    });
    const session = createWalletSession(wallet);
    const firstConnect = session.connect();
    await waitForRequest(wallet, "eth_call");
    await session.connect();
    const latestSnapshot = session.getSnapshot();
    staleTokenRead.resolve(`0x${"00".repeat(31)}2a`);
    await firstConnect;
    expect(session.getSnapshot()).toEqual(latestSnapshot);

    expect(wallet.listenerCount("accountsChanged")).toBe(1);
    expect(wallet.listenerCount("chainChanged")).toBe(1);
    expect(wallet.listenerCount("disconnect")).toBe(1);
    session.dispose();
    expect(wallet.listenerCount("accountsChanged")).toBe(0);
    expect(wallet.listenerCount("chainChanged")).toBe(0);
    expect(wallet.listenerCount("disconnect")).toBe(0);
    wallet.emit("accountsChanged", ["0x2222222222222222222222222222222222222222"]);
    expect(session.getSnapshot()).toMatchObject({ account: null, balances: null, status: "disconnected" });
  });
});
