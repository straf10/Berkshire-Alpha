import type { Trade } from "@/lib/types";

// The order-outcome vocabulary, translated once so every table reads the same.
//
// Two enums meet in a Trade row and they are NOT interchangeable:
//   status      FILLED | PARTIAL_SUSPENDED | REJECTED | UNFILLED_REJECT
//               (agent/execution/order_manager.py's WalkResult)
//   reject_code RejectCode (agent/schemas/execution.py) -- what the BROKER said
//
// The old `reject_code ?? status` rendering conflated them, which mattered
// most for UNFILLED_REJECT: that status means the limit walk reached the
// AGENT'S OWN price cap without filling and cancelled rather than pay up
// (order_manager.py:158-164), i.e. a rule the agent obeyed, not a broker
// failure. Four of the eight trades placed so far are that case, and all four
// rendered as red `UNFILLED_REJECT` badges.
//
// Rule: derive the label from `status` first; only consult `reject_code` when
// the broker actually rejected the order (`status === "REJECTED"`). A real row
// exists with status=UNFILLED_REJECT and reject_code=UNKNOWN -- under the old
// precedence it printed a meaningless red "UNKNOWN".

export type TradeTone = "filled" | "capped" | "partial" | "rejected" | "unknown";

export interface TradeOutcome {
  label: string;
  tone: TradeTone;
  tip: string;
}

// Broker-reported reasons. Only reachable via status === "REJECTED".
const BROKER_REJECTS: Record<string, { label: string; tip: string }> = {
  INSUFFICIENT_BUYING_POWER: {
    label: "Rejected — buying power",
    tip: "The broker refused the order: not enough buying power for the spread's margin requirement.",
  },
  OPTIONS_LEVEL_NOT_PERMITTED: {
    label: "Rejected — options level",
    tip: "The broker refused the order: the account's options level does not permit this structure.",
  },
  CONTRACT_NOT_FOUND: {
    label: "Rejected — contract not found",
    tip: "The broker refused the order: an OCC symbol in the spread was not tradeable at submit time.",
  },
  MARKET_CLOSED: {
    label: "Rejected — market closed",
    tip: "The broker refused the order: submitted outside regular trading hours.",
  },
  MALFORMED_ORDER: {
    label: "Rejected — malformed order",
    tip: "The broker refused the order's structure.",
  },
};

const REJECT_REASON_NOT_REPORTED: TradeOutcome = {
  label: "Rejected — reason not reported",
  tone: "rejected",
  tip: "The broker refused the order but returned no reject code.",
};

// Widened to a structural Pick so OpenPositionsTable (whose rows are
// OpenPosition, not Trade) shares one vocabulary instead of printing raw
// enum values beside a table that translates them.
export function tradeOutcome(trade: Pick<Trade, "status" | "reject_code">): TradeOutcome {
  switch (trade.status) {
    case "FILLED":
      return {
        label: "Filled",
        tone: "filled",
        tip: "The limit order was walked to a fill on Alpaca's paper book.",
      };
    case "PARTIAL_SUSPENDED":
      return {
        label: "Partial fill — suspended",
        tone: "partial",
        tip:
          "Some contracts filled and the walk stopped rather than chase the rest. " +
          "The filled portion is a real position; the remainder was cancelled.",
      };
    case "UNFILLED_REJECT":
      return {
        label: "Cancelled at price cap",
        tone: "capped",
        tip:
          "The limit walk reached the agent's own price ceiling without filling, so it cancelled " +
          "instead of paying up. Nothing was traded and nothing was lost — this is the walk cap " +
          "doing its job, not a broker rejection.",
      };
    case "REJECTED": {
      const broker = trade.reject_code ? BROKER_REJECTS[trade.reject_code] : undefined;
      if (broker) return { label: broker.label, tone: "rejected", tip: broker.tip };
      return REJECT_REASON_NOT_REPORTED;
    }
    default:
      // Passthrough, never blank: a status this map has not seen yet still
      // renders something a reader can grep for in agent/.
      return {
        label: trade.status,
        tone: "unknown",
        tip: "Status reported by the execution layer with no display mapping yet.",
      };
  }
}

// Deliberately NOT the same axis as tradeOutcome(). "The order was cancelled"
// and "the position is open" are different questions, and the old table
// answered them in one column -- printing the word "open" in Realized P&L,
// where it collided with the Status column's own vocabulary.
export type PositionOutcome = "Open" | "Closed" | "Never opened";

export function positionOutcome(trade: Pick<Trade, "closed_at" | "filled_qty">): PositionOutcome {
  if (trade.closed_at !== null) return "Closed";
  if (trade.filled_qty > 0) return "Open";
  return "Never opened";
}

export const POSITION_TIP: Record<PositionOutcome, string> = {
  Open: "Contracts filled and the spread is still held.",
  Closed: "The spread was exited — by profit target, stop loss, DTE, assignment, or the unwind.",
  "Never opened": "No contracts filled, so no position was ever created.",
};

// Badge styling per tone. `capped` and `partial` use --warn, not
// --destructive: they are outcomes the agent CHOSE -- it hit its own price cap
// and cancelled -- not failures. That distinction is exactly what --warn was
// added for, and it is why these are not red.
export const TONE_CLASS: Record<TradeTone, string> = {
  filled: "",
  capped: "border-warn/30 bg-warn/10 text-warn",
  partial: "border-warn/30 bg-warn/10 text-warn",
  rejected: "",
  unknown: "",
};

export const TONE_VARIANT: Record<TradeTone, "default" | "destructive" | "secondary" | "outline"> = {
  filled: "default",
  capped: "outline",
  partial: "outline",
  rejected: "destructive",
  unknown: "secondary",
};

// One row per distinct outcome the table can show, for the legend. Order is
// best-to-worst so the legend reads as a scale.
export const OUTCOME_LEGEND: { label: string; tone: TradeTone; tip: string }[] = [
  {
    label: "Filled",
    tone: "filled",
    tip: "Walked to a fill on Alpaca's paper book.",
  },
  {
    label: "Cancelled at price cap",
    tone: "capped",
    tip: "The walk hit the agent's own price ceiling and cancelled rather than pay up.",
  },
  {
    label: "Partial fill — suspended",
    tone: "partial",
    tip: "Some contracts filled; the walk stopped rather than chase the rest.",
  },
  {
    label: "Rejected — …",
    tone: "rejected",
    tip: "The broker refused the order. The suffix names the broker's reject code.",
  },
];
