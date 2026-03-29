import {
  HealthResponse,
  ViewerSession,
  ViewerTimelineV1,
  parseHealthResponse,
  parseViewerSession,
  parseViewerTimeline,
} from './types';

async function fetchJson(path: string): Promise<unknown> {
  const response = await fetch(path, {
    headers: {
      Accept: 'application/json',
    },
  });

  if (!response.ok) {
    throw new Error(`${path} returned HTTP ${response.status}`);
  }

  return response.json();
}

export async function fetchHealth(): Promise<HealthResponse> {
  const payload = await fetchJson('/api/health');
  return parseHealthResponse(payload);
}

export async function fetchViewerSession(): Promise<ViewerSession> {
  const payload = await fetchJson('/api/viewer/session');
  return parseViewerSession(payload);
}

export async function fetchViewerTimeline(): Promise<ViewerTimelineV1> {
  const payload = await fetchJson('/api/viewer/timeline');
  return parseViewerTimeline(payload);
}

export async function fetchViewerPlayhead(): Promise<number> {
  const payload = await fetchJson('/api/viewer/playhead');
  const record = payload as Record<string, unknown>;
  const value = record?.playhead_us;
  return typeof value === 'number' && Number.isFinite(value) ? value : 0;
}
