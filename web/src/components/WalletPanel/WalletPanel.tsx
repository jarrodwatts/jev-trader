"use client";

import { useOkxWallet } from "@/lib/useOkxWallet";
import styles from "./WalletPanel.module.css";

function statusLabel(status: string): string {
  switch (status) {
    case "connecting":
      return "Aguardando autorização no OKX";
    case "connected":
      return "Conectado à Monad";
    case "wrong-network":
      return "Troque para a rede Monad";
    case "reading":
      return "Consultando a carteira";
    case "error":
      return "Não foi possível concluir a consulta";
    default:
      return "Desconectado";
  }
}

function shortAddress(address: string): string {
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

export default function WalletPanel() {
  const { available, ready, snapshot, connect, refresh, switchToMonad, disconnect } =
    useOkxWallet();
  const busy = snapshot.status === "connecting" || snapshot.status === "reading";

  return (
    <section className={styles.panel} aria-labelledby="wallet-title">
      <header className={styles.header}>
        <div>
          <h2 className={styles.title} id="wallet-title">
            Carteira OKX
          </h2>
          <p className={styles.description}>
            Somente leitura. Conectar não habilita o executor.
          </p>
        </div>
        <span className={styles.badge}>Web3</span>
      </header>

      <div className={styles.content}>
        <div className={styles.statusRow} aria-live="polite" aria-atomic="true">
          <span
            className={`${styles.statusDot} ${
              snapshot.status === "connected" ? styles.statusDotConnected : ""
            }`}
            aria-hidden="true"
          />
          <span>{statusLabel(snapshot.status)}</span>
        </div>

        {!ready ? (
          <p className={styles.note}>Verificando a extensão OKX neste navegador…</p>
        ) : !available && !snapshot.account ? (
          <p className={styles.note}>
            Extensão OKX não detectada. Abra este painel em um navegador com a extensão instalada.
          </p>
        ) : null}

        {snapshot.account ? (
          <dl className={styles.details}>
            <div className={styles.detailRow}>
              <dt>Conta</dt>
              <dd title={snapshot.account}>{shortAddress(snapshot.account)}</dd>
            </div>
            <div className={styles.detailRow}>
              <dt>Rede</dt>
              <dd>
                {snapshot.chainId === "0x8f"
                  ? "Monad (143)"
                  : snapshot.chainId ?? "Não confirmada"}
              </dd>
            </div>
          </dl>
        ) : null}

        {snapshot.balances ? (
          <dl className={styles.balances} aria-label="Saldos da carteira">
            <div className={styles.balanceRow}>
              <dt>MON</dt>
              <dd>{snapshot.balances.mon}</dd>
            </div>
            <div className={styles.balanceRow}>
              <dt>USDC</dt>
              <dd>{snapshot.balances.usdc}</dd>
            </div>
            <div className={styles.blockRow}>
              <dt>Bloco consultado</dt>
              <dd>{snapshot.balances.block}</dd>
            </div>
          </dl>
        ) : snapshot.status === "reading" ? (
          <p className={styles.note}>Saldos indisponíveis enquanto a consulta termina.</p>
        ) : snapshot.account && snapshot.status === "connected" ? (
          <p className={styles.note}>Saldos ainda não consultados. Atualize para ler.</p>
        ) : null}

        {snapshot.error ? (
          <p className={styles.error} role="alert">
            {snapshot.error}
          </p>
        ) : null}

        <div className={styles.actions}>
          {snapshot.account ? (
            <>
              {snapshot.status === "wrong-network" ? (
                <button
                  className={styles.primaryButton}
                  type="button"
                  onClick={() => void switchToMonad()}
                  disabled={busy || !available}
                >
                  Trocar para Monad
                </button>
              ) : null}
              <button
                className={styles.secondaryButton}
                type="button"
                onClick={() => void refresh()}
                disabled={busy || !available}
              >
                Atualizar saldo
              </button>
              <button
                className={styles.textButton}
                type="button"
                onClick={disconnect}
                disabled={busy}
              >
                Desconectar deste painel
              </button>
            </>
          ) : (
            <button
              className={styles.primaryButton}
              type="button"
              onClick={() => void connect()}
              disabled={!ready || !available || busy}
            >
              Conectar OKX
            </button>
          )}
        </div>
        <p className={styles.footer}>
          Este painel apenas consulta saldos. Nenhuma ordem ou transação é enviada.
        </p>
      </div>
    </section>
  );
}
