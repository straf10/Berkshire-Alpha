import { Settings } from "lucide-react";
import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { AgentConfig } from "@/lib/types";

function ConfigRow({
  label,
  value,
}: {
  label: string;
  value: string | number;
}) {
  return (
    <div className="flex justify-between gap-4 py-1">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-mono font-semibold tabular-nums">{value}</span>
    </div>
  );
}

function ConfigGroup({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="rounded-md border border-border p-3">
      <p className="mb-1 text-subheadline font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <div className="divide-y divide-border/50">{children}</div>
    </div>
  );
}

export function AgentConfigPanel({ config }: { config: AgentConfig | null }) {
  if (config === null) return null;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-1.5 text-subheadline font-semibold uppercase tracking-wide text-muted-foreground">
          <Settings className="size-3.5" />
          Agent configuration
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-1 gap-3 text-base md:grid-cols-2 lg:grid-cols-3">
          <ConfigGroup title="Universe">
            <ConfigRow
              label="Tickers"
              value={config.universe.tickers.join(", ")}
            />
            <ConfigRow
              label="Earnings verified on"
              value={config.universe.earnings_verified_on}
            />
          </ConfigGroup>

          <ConfigGroup title="Exit rules">
            <ConfigRow
              label="Profit target"
              value={`${Number(config.exit_rules.profit_target_pct_of_max) * 100}% of max`}
            />
            <ConfigRow
              label="Credit stop-loss"
              value={`${Number(config.exit_rules.credit_stop_loss_pct) * 100}% of credit`}
            />
            <ConfigRow
              label="Debit stop-loss"
              value={`${Number(config.exit_rules.debit_stop_loss_pct) * 100}% of debit`}
            />
            <ConfigRow
              label="Time stop"
              value={`< ${config.exit_rules.dte_force_close} DTE`}
            />
            <ConfigRow
              label="Unwind"
              value={`${config.exit_rules.unwind_date} ${config.exit_rules.unwind_et_time} ET`}
            />
            <ConfigRow
              label="Priority order"
              value={config.exit_rules.priority_order.join(" > ")}
            />
          </ConfigGroup>

          <ConfigGroup title="Position sizing">
            <ConfigRow
              label="Kelly fraction"
              value={config.sizing.kelly_fraction}
            />
            <ConfigRow
              label="Max risk / trade"
              value={`${config.sizing.max_risk_per_trade_pct * 100}%`}
            />
            <ConfigRow
              label="Max aggregate risk"
              value={`${config.sizing.max_aggregate_risk_pct * 100}%`}
            />
            <ConfigRow
              label="Max concurrent positions"
              value={config.sizing.max_concurrent_positions}
            />
            <ConfigRow
              label="Max per underlying"
              value={config.sizing.max_positions_per_underlying}
            />
            <ConfigRow
              label="Portfolio delta limit"
              value={`${config.sizing.portfolio_delta_pct * 100}%`}
            />
            <ConfigRow
              label="Portfolio vega limit"
              value={`${config.sizing.portfolio_vega_pct * 100}%`}
            />
            <ConfigRow
              label="Starting equity"
              value={`$${Number(config.sizing.account_start_equity).toLocaleString()}`}
            />
          </ConfigGroup>

          <ConfigGroup title="Risk gates">
            <ConfigRow
              label="Daily loss kill switch"
              value={`${config.risk_gates.daily_loss_kill_pct * 100}%`}
            />
            <ConfigRow
              label="Conservative drawdown"
              value={`${config.risk_gates.drawdown_conservative_pct * 100}%`}
            />
            <ConfigRow
              label="Terminal drawdown"
              value={`${config.risk_gates.drawdown_terminal_pct * 100}%`}
            />
            <ConfigRow
              label="Conviction floor (grounded)"
              value={config.risk_gates.conviction_grounding_floor}
            />
            <ConfigRow
              label="Conviction floor (degraded)"
              value={config.risk_gates.conviction_degraded_floor}
            />
            <ConfigRow
              label="Entry cutoff"
              value={`${Math.abs(config.risk_gates.entry_cutoff_offset_min)}m before close`}
            />
            <ConfigRow
              label="DTE window"
              value={`${config.risk_gates.dte_min}–${config.risk_gates.dte_max}`}
            />
          </ConfigGroup>

          <ConfigGroup title="Regime thresholds">
            <ConfigRow
              label="RSI period"
              value={config.regime_thresholds.rsi_period}
            />
            <ConfigRow
              label="RSI overbought / oversold"
              value={`${config.regime_thresholds.rsi_overbought} / ${config.regime_thresholds.rsi_oversold}`}
            />
            <ConfigRow
              label="VWAP deviation threshold"
              value={`${config.regime_thresholds.vwap_dev_threshold_pct * 100}%`}
            />
            <ConfigRow
              label="VWM z-score (strong)"
              value={config.regime_thresholds.vwm_z_strong}
            />
            <ConfigRow
              label="Cross-section N"
              value={config.regime_thresholds.cross_section_n}
            />
            <ConfigRow
              label="RV winsorization z"
              value={config.regime_thresholds.rv_winsor_z}
            />
          </ConfigGroup>

          <ConfigGroup title="Scan schedule">
            <ConfigRow
              label="Max shortlist"
              value={config.scan_schedule.shortlist_max}
            />
            <ConfigRow
              label="Scan offsets"
              value={config.scan_schedule.scan_offsets_min
                .map((m) => `open +${m}m`)
                .join(", ")}
            />
            <ConfigRow
              label="Management tick"
              value={`every ${config.scan_schedule.management_interval_s / 60}m`}
            />
            <ConfigRow
              label="Closed-market sleep cap"
              value={`${config.scan_schedule.closed_sleep_ceiling_s / 60}m`}
            />
          </ConfigGroup>

          <ConfigGroup title="LLM">
            <ConfigRow
              label="Provider / model"
              value={`${config.llm.provider} / ${config.llm.model}`}
            />
            <ConfigRow label="Temperature" value={config.llm.temperature} />
            <ConfigRow label="Max tokens" value={config.llm.max_tokens} />
            <ConfigRow label="Timeout" value={`${config.llm.timeout_s}s`} />
            <ConfigRow
              label="Debate rounds / candidates"
              value={`${config.llm.debate_max_rounds} / ${config.llm.debate_candidates}`}
            />
            <ConfigRow
              label="Consensus threshold"
              value={config.llm.consensus_high_threshold}
            />
            <ConfigRow
              label="Cost in / out per Mtok"
              value={`$${config.llm.cost_in_per_mtok_usd} / $${config.llm.cost_out_per_mtok_usd}`}
            />
          </ConfigGroup>

          <ConfigGroup title="Rate limits">
            <ConfigRow
              label="Market data concurrency"
              value={config.rate_limits.market_data_concurrency}
            />
            <ConfigRow
              label="LLM concurrency"
              value={config.rate_limits.llm_concurrency}
            />
            <ConfigRow
              label="LLM calls / session cap"
              value={config.rate_limits.llm_calls_per_session}
            />
            <ConfigRow
              label="LLM daily spend ceiling"
              value={`$${config.rate_limits.llm_daily_spend_ceiling_usd}`}
            />
            <ConfigRow
              label="Order walk poll interval"
              value={`${config.rate_limits.order_walk_poll_interval_s}s`}
            />
            <ConfigRow
              label="Partial fill max poll"
              value={`${config.rate_limits.partial_fill_max_poll_s / 60}m`}
            />
            <ConfigRow
              label="Assignment order poll"
              value={`${config.rate_limits.assignment_order_poll_s}s`}
            />
          </ConfigGroup>

          <ConfigGroup title="Tools">
            {config.tools.map((t) => (
              <div key={t.name} className="py-1">
                <span className="font-semibold">{t.name}</span>
                <span className="text-muted-foreground"> — {t.purpose}</span>
              </div>
            ))}
          </ConfigGroup>
        </div>
      </CardContent>
    </Card>
  );
}
