import {
  ConnectionState,
  ViewerFrameMessage,
  ViewerHeartbeatMessage,
  ViewerHelloMessage,
  parseViewerSocketMessage,
} from './types';

export interface ViewerSocketHandlers {
  onConnect?: () => void;
  onDisconnect?: () => void;
  onHello?: (message: ViewerHelloMessage) => void;
  onFrame?: (frame: ViewerFrameMessage) => void;
  onHeartbeat?: (message: ViewerHeartbeatMessage) => void;
  onStateChange?: (state: ConnectionState) => void;
  onError?: (message: string) => void;
}

export interface ViewerSocketOptions {
  minDelayMs?: number;
  maxDelayMs?: number;
  backoffFactor?: number;
  jitterMs?: number;
  inactivityTimeoutMs?: number;
  watchdogIntervalMs?: number;
}

const DEFAULT_OPTIONS: Required<ViewerSocketOptions> = {
  minDelayMs: 500,
  maxDelayMs: 12000,
  backoffFactor: 1.8,
  jitterMs: 450,
  inactivityTimeoutMs: 20000,
  watchdogIntervalMs: 5000,
};

function normalizeSocketUrl(pathOrUrl: string): string {
  if (pathOrUrl.startsWith('ws://') || pathOrUrl.startsWith('wss://')) {
    return pathOrUrl;
  }

  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const path = pathOrUrl.startsWith('/') ? pathOrUrl : `/${pathOrUrl}`;

  return `${protocol}://${window.location.host}${path}`;
}

export class ViewerSocketClient {
  private socket: WebSocket | null = null;
  private reconnectTimer: number | null = null;
  private watchdogTimer: number | null = null;
  private reconnectAttempt = 0;
  private shouldReconnect = false;
  private lastMessageAt = 0;
  private handlers: ViewerSocketHandlers = {};
  private state: ConnectionState = 'idle';

  private readonly socketUrl: string;
  private readonly options: Required<ViewerSocketOptions>;

  constructor(pathOrUrl: string, options?: ViewerSocketOptions) {
    this.socketUrl = normalizeSocketUrl(pathOrUrl);
    this.options = {
      ...DEFAULT_OPTIONS,
      ...options,
    };
  }

  setHandlers(handlers: ViewerSocketHandlers): void {
    this.handlers = handlers;
  }

  connect(): void {
    if (this.shouldReconnect) {
      return;
    }

    this.shouldReconnect = true;
    this.open(false);
  }

  disconnect(): void {
    this.shouldReconnect = false;
    this.clearReconnectTimer();
    this.clearWatchdogTimer();

    if (
      this.socket &&
      (this.socket.readyState === WebSocket.OPEN ||
        this.socket.readyState === WebSocket.CONNECTING)
    ) {
      this.socket.close(1000, 'manual-close');
    }

    this.socket = null;
    this.setState('disconnected');
  }

  private open(fromReconnect: boolean): void {
    if (
      this.socket &&
      (this.socket.readyState === WebSocket.OPEN ||
        this.socket.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    this.setState(fromReconnect ? 'reconnecting' : 'connecting');

    let socket: WebSocket;
    try {
      socket = new WebSocket(this.socketUrl);
    } catch {
      this.handlers.onError?.('Viewer socket open failed. Retrying...');
      this.scheduleReconnect();
      return;
    }

    this.socket = socket;

    socket.onopen = () => {
      if (socket !== this.socket) {
        return;
      }

      this.reconnectAttempt = 0;
      this.lastMessageAt = Date.now();
      this.clearReconnectTimer();
      this.startWatchdogTimer();
      this.setState('connected');
      this.handlers.onConnect?.();
    };

    socket.onmessage = (event) => {
      if (socket !== this.socket) {
        return;
      }

      this.lastMessageAt = Date.now();
      const message = parseViewerSocketMessage(event.data);
      if (!message) {
        return;
      }

      switch (message.type) {
        case 'hello':
          this.handlers.onHello?.(message);
          break;
        case 'heartbeat':
          this.handlers.onHeartbeat?.(message);
          break;
        case 'frame':
          this.handlers.onFrame?.(message);
          break;
        default:
          break;
      }
    };

    socket.onerror = () => {
      if (socket !== this.socket) {
        return;
      }

      this.handlers.onError?.('Viewer socket error. Reconnecting...');
    };

    socket.onclose = () => {
      if (socket !== this.socket) {
        return;
      }

      this.clearWatchdogTimer();
      this.socket = null;
      this.handlers.onDisconnect?.();

      if (!this.shouldReconnect) {
        this.setState('disconnected');
        return;
      }

      this.scheduleReconnect();
    };
  }

  private scheduleReconnect(): void {
    this.clearReconnectTimer();

    this.reconnectAttempt += 1;
    const baseDelay =
      this.options.minDelayMs *
      Math.pow(this.options.backoffFactor, this.reconnectAttempt - 1);
    const jitter = Math.random() * this.options.jitterMs;
    const delay = Math.min(this.options.maxDelayMs, Math.round(baseDelay + jitter));

    this.setState('reconnecting');

    this.reconnectTimer = window.setTimeout(() => {
      this.open(true);
    }, delay);
  }

  private startWatchdogTimer(): void {
    this.clearWatchdogTimer();

    this.watchdogTimer = window.setInterval(() => {
      if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
        return;
      }

      const inactiveMs = Date.now() - this.lastMessageAt;
      if (inactiveMs <= this.options.inactivityTimeoutMs) {
        return;
      }

      this.handlers.onError?.('Viewer stream heartbeat timeout. Reconnecting...');
      this.socket.close(4000, 'viewer-heartbeat-timeout');
    }, this.options.watchdogIntervalMs);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer === null) {
      return;
    }

    window.clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private clearWatchdogTimer(): void {
    if (this.watchdogTimer === null) {
      return;
    }

    window.clearInterval(this.watchdogTimer);
    this.watchdogTimer = null;
  }

  private setState(nextState: ConnectionState): void {
    if (this.state === nextState) {
      return;
    }

    this.state = nextState;
    this.handlers.onStateChange?.(nextState);
  }
}
