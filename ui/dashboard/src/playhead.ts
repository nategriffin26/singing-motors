export const START_DETECT_POLL_MS = 16;
export const START_DETECT_MIN_PLAYHEAD_US = 12_000;

export function toViewerPlayheadUs(motorPlayheadUs: number, syncOffsetMs: number): number {
  if (motorPlayheadUs <= 0) {
    return 0;
  }
  return Math.max(0, Math.round((motorPlayheadUs / 1000 - syncOffsetMs) * 1000));
}

export function shouldPollForPlaybackStart(playbackStartPerfMs: number | null): boolean {
  return playbackStartPerfMs === null;
}

export function hasPlaybackStarted(playheadUs: number): boolean {
  return playheadUs >= START_DETECT_MIN_PLAYHEAD_US;
}

export function scheduledStartToPerfMs(
  scheduledStartUnixMs: number,
  syncOffsetMs: number,
  nowUnixMs: number,
  nowPerfMs: number,
): number {
  return nowPerfMs + (scheduledStartUnixMs + syncOffsetMs - nowUnixMs);
}
