"use client";

/**
 * PipelineStatus -- real-time pipeline progress visualization.
 *
 * Connects to the backend WebSocket for live updates, falls back to
 * REST polling if WS is unavailable. Shows:
 *   - Overall progress bar with percentage
 *   - Step-by-step timeline with status icons
 *   - Current step message
 *   - Cancel / Retry buttons
 *   - Elapsed time
 *
 * Props:
 *   analysisId: number
 *   accessToken: string (JWT for WS auth + REST calls)
 *   onComplete: optional callback when pipeline finishes
 */

import { useState, useEffect, useRef, useCallback } from "react";

// -- Types matching the backend WS/REST responses --

interface PipelineStep {
  key: string;
  label: string;
  weight: number;
}

interface StepStatus {
  status: string;
  message: string | null;
  timestamp: string | null;
}

interface SnapshotMessage {
  type: "snapshot";
  analysis_id: number;
  analysis_status: string;
  progress_pct: number;
  steps: PipelineStep[];
  step_status: Record<string, StepStatus>;
  job_logs: Array<{
    step: string;
    status: string;
    message: string | null;
    timestamp: string | null;
  }>;
}

interface ProgressMessage {
  type: "progress";
  analysis_id: number;
  step: string;
  status: string;
  message: string;
  progress_pct: number;
  timestamp: string;
  terminal?: boolean;
}

interface ErrorMessage {
  type: "error";
  message: string;
}

type WSMessage = SnapshotMessage | ProgressMessage | ErrorMessage | { type: "pong" } | { type: "terminal"; status: string; message: string };

// -- Status icon styles --

const STEP_ICONS: Record<string, { icon: string; color: string }> = {
  pending:  { icon: "○", color: "text-gray-300 dark:text-gray-600" },
  running:  { icon: "◉", color: "text-blue-500" },
  complete: { icon: "●", color: "text-emerald-500" },
  failed:   { icon: "✕", color: "text-red-500" },
};

// -- Backend URL for WebSocket (direct, not through Next.js proxy) --

function getWsUrl(analysisId: number, token: string): string {
  // In production, this would come from an env var.
  // WS must connect directly to backend since Next.js rewrites don't proxy WS.
  const backendBase =
    typeof window !== "undefined"
      ? process.env.NEXT_PUBLIC_WS_URL || `ws://${window.location.hostname}:8000`
      : "ws://localhost:8000";
  return `${backendBase}/api/analyses/${analysisId}/ws?token=${encodeURIComponent(token)}`;
}

function getApiBase(): string {
  return "/api/py";
}

// -- Component --

interface PipelineStatusProps {
  analysisId: number;
  accessToken: string;
  onComplete?: (status: string) => void;
}

export default function PipelineStatus({
  analysisId,
  accessToken,
  onComplete,
}: PipelineStatusProps) {
  // Pipeline state
  const [steps, setSteps] = useState<PipelineStep[]>([]);
  const [stepStatus, setStepStatus] = useState<Record<string, StepStatus>>({});
  const [analysisStatus, setAnalysisStatus] = useState<string>("queued");
  const [progressPct, setProgressPct] = useState(0);
  const [currentMessage, setCurrentMessage] = useState<string>("Waiting for pipeline...");
  const [startedAt, setStartedAt] = useState<Date | null>(null);
  const startedAtRef = useRef<Date | null>(null); // ref mirror to avoid dep cycle
  const [elapsed, setElapsed] = useState<string>("--");

  // UI state
  const [wsConnected, setWsConnected] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Incrementing this forces the WS effect to reconnect (used after retry)
  const [wsReconnectKey, setWsReconnectKey] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const stepsLoadedRef = useRef(false); // track if steps have been loaded (for polling)
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const elapsedIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete; // always-fresh ref for callbacks

  const isTerminal = ["complete", "failed", "cancelled"].includes(analysisStatus);

  // -- Elapsed timer --
  useEffect(() => {
    if (!startedAt || isTerminal) return;

    const update = () => {
      const diff = Date.now() - startedAt.getTime();
      const secs = Math.floor(diff / 1000);
      const mins = Math.floor(secs / 60);
      const hrs = Math.floor(mins / 60);
      if (hrs > 0) {
        setElapsed(`${hrs}h ${mins % 60}m`);
      } else if (mins > 0) {
        setElapsed(`${mins}m ${secs % 60}s`);
      } else {
        setElapsed(`${secs}s`);
      }
    };
    update();
    elapsedIntervalRef.current = setInterval(update, 1000);
    return () => {
      if (elapsedIntervalRef.current) clearInterval(elapsedIntervalRef.current);
    };
  }, [startedAt, isTerminal]);

  // Helper: set startedAt in both state and ref
  const markStarted = useCallback((d: Date) => {
    startedAtRef.current = d;
    setStartedAt(d);
  }, []);

  // -- Process incoming WS messages --
  // No state deps in the dependency array -- uses refs for startedAt and onComplete
  // to avoid circular re-creation that would tear down the WebSocket.
  const handleMessage = useCallback(
    (msg: WSMessage) => {
      if (msg.type === "snapshot") {
        const snap = msg as SnapshotMessage;
        setSteps(snap.steps);
        stepsLoadedRef.current = true;
        setStepStatus(snap.step_status);
        setAnalysisStatus(snap.analysis_status);
        setProgressPct(snap.progress_pct);

        // Find the latest running/complete log for current message
        const logs = snap.job_logs;
        if (logs.length > 0) {
          const last = logs[logs.length - 1];
          setCurrentMessage(last.message || last.step);
          const firstTs = logs[0].timestamp;
          if (firstTs) markStarted(new Date(firstTs));
        }
      } else if (msg.type === "progress") {
        const prog = msg as ProgressMessage;
        setProgressPct(prog.progress_pct);
        setCurrentMessage(prog.message);
        // A step completing doesn't mean the analysis is complete --
        // the analysis stays "running" until the terminal event.
        setAnalysisStatus(prog.status === "complete" ? "running" : prog.status);

        // Update step status
        setStepStatus((prev) => ({
          ...prev,
          [prog.step]: {
            status: prog.status,
            message: prog.message,
            timestamp: prog.timestamp,
          },
        }));

        if (!startedAtRef.current && prog.timestamp) {
          markStarted(new Date(prog.timestamp));
        }

        // Terminal event (pipeline done/failed) -- close WS, no more messages expected
        if (prog.terminal) {
          const finalStatus = prog.status;
          setAnalysisStatus(finalStatus);
          if (finalStatus === "complete") setProgressPct(1.0);
          onCompleteRef.current?.(finalStatus);
          // Clean close -- server won't send anything else
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            wsRef.current.close();
          }
        }
      } else if (msg.type === "terminal") {
        const term = msg as { type: "terminal"; status: string; message: string };
        setAnalysisStatus(term.status);
        setCurrentMessage(term.message);
        if (term.status === "complete") setProgressPct(1.0);
        onCompleteRef.current?.(term.status);
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          wsRef.current.close();
        }
      } else if (msg.type === "error") {
        setError((msg as ErrorMessage).message);
      }
    },
    [markStarted]
  );

  // -- WebSocket connection --
  useEffect(() => {
    if (!accessToken || !analysisId) return;

    const url = getWsUrl(analysisId, accessToken);
    let ws: WebSocket;

    try {
      ws = new WebSocket(url);
    } catch {
      // WS not available, fall through to polling
      return;
    }

    ws.onopen = () => {
      setWsConnected(true);
      setError(null);
      // Keepalive ping every 30s
      pingIntervalRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ action: "ping" }));
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);
        handleMessage(msg);
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      setWsConnected(false);
      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
    };

    ws.onerror = () => {
      setWsConnected(false);
    };

    wsRef.current = ws;

    return () => {
      if (pingIntervalRef.current) clearInterval(pingIntervalRef.current);
      if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
        ws.close();
      }
    };
  }, [analysisId, accessToken, handleMessage, wsReconnectKey]);

  // -- REST polling fallback (if WS not connected and not terminal) --
  useEffect(() => {
    if (wsConnected || isTerminal) {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
      return;
    }

    const poll = async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/analyses/${analysisId}/status`, {
          headers: { Authorization: `Bearer ${accessToken}` },
        });
        if (!res.ok) return;
        const data = await res.json();

        setAnalysisStatus(data.status);
        setProgressPct(data.progress_pct);

        // Convert pipeline_steps to our format
        if (data.pipeline_steps) {
          const statusMap: Record<string, StepStatus> = {};
          for (const s of data.pipeline_steps) {
            if (s.status !== "pending") {
              statusMap[s.key] = {
                status: s.status,
                message: s.message,
                timestamp: s.timestamp,
              };
            }
          }
          setStepStatus(statusMap);
        }

        // Set steps from pipeline_steps definitions if we don't have them yet
        if (!stepsLoadedRef.current && data.pipeline_steps) {
          stepsLoadedRef.current = true;
          setSteps(
            data.pipeline_steps.map((s: { key: string; label: string }) => ({
              key: s.key,
              label: s.label,
              weight: 0,
            }))
          );
        }

        if (data.job_progress?.length > 0) {
          const last = data.job_progress[data.job_progress.length - 1];
          setCurrentMessage(last.message || last.step);
        }

        if (["complete", "failed", "cancelled"].includes(data.status)) {
          onCompleteRef.current?.(data.status);
        }
      } catch {
        // Polling failures are non-critical
      }
    };

    poll(); // immediate first poll
    pollIntervalRef.current = setInterval(poll, 3000);
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
    };
  }, [wsConnected, isTerminal, analysisId, accessToken]);

  // -- Cancel handler --
  const handleCancel = useCallback(async () => {
    setCancelling(true);
    try {
      const res = await fetch(`${getApiBase()}/api/analyses/${analysisId}/cancel`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (res.ok) {
        setAnalysisStatus("cancelled");
        setCurrentMessage("Cancelled by user");
      } else {
        const data = await res.json();
        setError(data.detail || "Failed to cancel");
      }
    } catch {
      setError("Network error cancelling analysis");
    } finally {
      setCancelling(false);
    }
  }, [analysisId, accessToken]);

  // -- Retry handler --
  const handleRetry = useCallback(async () => {
    setRetrying(true);
    setError(null);
    try {
      const res = await fetch(`${getApiBase()}/api/analyses/${analysisId}/retry`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      });
      if (res.ok) {
        setAnalysisStatus("queued");
        setProgressPct(0);
        setStepStatus({});
        setCurrentMessage("Re-queued for processing...");
        setStartedAt(null);
        startedAtRef.current = null;
        stepsLoadedRef.current = false;
        setElapsed("--");
        // Force WS reconnect by changing the dep key
        setWsReconnectKey((k) => k + 1);
      } else {
        const data = await res.json();
        setError(data.detail || "Failed to retry");
      }
    } catch {
      setError("Network error retrying analysis");
    } finally {
      setRetrying(false);
    }
  }, [analysisId, accessToken]);

  // -- Status badge --
  const statusBadge = () => {
    const styles: Record<string, string> = {
      queued: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
      running: "bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
      complete: "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
      failed: "bg-red-50 text-red-700 dark:bg-red-900/30 dark:text-red-400",
      cancelled: "bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
    };
    const labels: Record<string, string> = {
      queued: "Queued",
      running: "Running",
      complete: "Complete",
      failed: "Failed",
      cancelled: "Cancelled",
    };
    return (
      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${styles[analysisStatus] || styles.queued}`}>
        {analysisStatus === "running" && (
          <span className="w-1.5 h-1.5 rounded-full bg-blue-500 mr-1.5 animate-pulse" />
        )}
        {labels[analysisStatus] || analysisStatus}
      </span>
    );
  };

  // Progress bar color
  const barColor = analysisStatus === "failed"
    ? "bg-red-500"
    : analysisStatus === "cancelled"
    ? "bg-amber-500"
    : analysisStatus === "complete"
    ? "bg-emerald-500"
    : "bg-blue-500";

  const pctDisplay = Math.round(progressPct * 100);

  return (
    <div className="space-y-5">
      {/* Header row: status badge + elapsed + connection indicator */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {statusBadge()}
          <span className="text-sm text-muted-foreground">{elapsed}</span>
        </div>
        <div className="flex items-center gap-2">
          {wsConnected && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
              Live
            </span>
          )}
          {!wsConnected && !isTerminal && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-500" />
              Polling
            </span>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <span className="text-sm font-medium text-foreground">
            {isTerminal ? (analysisStatus === "complete" ? "Pipeline complete" : `Pipeline ${analysisStatus}`) : currentMessage}
          </span>
          <span className="text-sm tabular-nums text-muted-foreground">{pctDisplay}%</span>
        </div>
        <div className="w-full bg-gray-100 dark:bg-gray-800 rounded-full h-2 overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-700 ease-out ${barColor}`}
            style={{ width: `${pctDisplay}%` }}
          />
        </div>
      </div>

      {/* Step timeline */}
      {steps.length > 0 && (
        <div className="relative pl-6">
          {/* Vertical line */}
          <div className="absolute left-[7px] top-1 bottom-1 w-px bg-gray-200 dark:bg-gray-700" />

          <div className="space-y-2.5">
            {steps
              .filter((s) => s.key !== "done") // hide the "done" meta-step
              .map((step) => {
                const info = stepStatus[step.key];
                const st = info?.status || "pending";
                const icon = STEP_ICONS[st] || STEP_ICONS.pending;
                const isActive = st === "running";

                return (
                  <div key={step.key} className="relative flex items-start gap-3">
                    {/* Icon dot */}
                    <span
                      className={`relative z-10 text-sm leading-none ${icon.color} ${isActive ? "animate-pulse" : ""}`}
                      style={{ marginLeft: "-19px" }}
                    >
                      {icon.icon}
                    </span>
                    {/* Label + message */}
                    <div className="min-w-0 flex-1 -mt-0.5">
                      <span
                        className={`text-sm ${
                          st === "complete"
                            ? "text-muted-foreground"
                            : st === "running"
                            ? "font-medium text-foreground"
                            : st === "failed"
                            ? "text-red-600 dark:text-red-400 font-medium"
                            : "text-muted-foreground/50"
                        }`}
                      >
                        {step.label}
                      </span>
                      {info?.message && st !== "pending" && (
                        <p className="text-xs text-muted-foreground mt-0.5 truncate">
                          {info.message}
                        </p>
                      )}
                    </div>
                    {/* Timestamp */}
                    {info?.timestamp && (
                      <span className="text-[10px] text-muted-foreground/60 tabular-nums whitespace-nowrap">
                        {new Date(info.timestamp).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </span>
                    )}
                  </div>
                );
              })}
          </div>
        </div>
      )}

      {/* Error message */}
      {error && (
        <div className="text-sm text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20 rounded-md px-3 py-2">
          {error}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex gap-2 pt-1">
        {!isTerminal && (
          <button
            onClick={handleCancel}
            disabled={cancelling}
            className="px-3 py-1.5 text-sm border border-red-200 dark:border-red-800 text-red-600 dark:text-red-400 rounded-md hover:bg-red-50 dark:hover:bg-red-900/20 transition disabled:opacity-50"
          >
            {cancelling ? "Cancelling..." : "Cancel"}
          </button>
        )}
        {(analysisStatus === "failed" || analysisStatus === "cancelled") && (
          <button
            onClick={handleRetry}
            disabled={retrying}
            className="px-3 py-1.5 text-sm bg-blue-500 text-white rounded-md hover:bg-blue-600 transition disabled:opacity-50"
          >
            {retrying ? "Retrying..." : "Retry Analysis"}
          </button>
        )}
      </div>
    </div>
  );
}
