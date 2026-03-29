import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from 'react';

import { fetchViewerPlayhead, fetchViewerSession, fetchViewerTimeline } from './api';
import {
  hasPlaybackStarted,
  scheduledStartToPerfMs,
  shouldPollForPlaybackStart,
  START_DETECT_POLL_MS,
  toViewerPlayheadUs,
} from './playhead';
import { ColorModeId, ThemeId, ViewerSession, ViewerTimelineBar, ViewerTimelineFrameV1, ViewerTimelineV1 } from './types';
import { buildSceneModel, CanvasViewport, clamp, listColorModeIds } from './themes/helpers';
import {
  applyVerticalSceneTransform,
  createUprightTextDrawer,
  toLogicalViewport,
} from './themes/orientation';
import { listThemeIds, themeById } from './themes/registry';

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim().length > 0) {
    return error.message;
  }
  return fallback;
}

function normalizeThemeCatalog(session: ViewerSession | null): ThemeId[] {
  const fallback = listThemeIds();
  if (!session?.themes_available || session.themes_available.length === 0) {
    return fallback;
  }

  const unique = new Set<ThemeId>();
  for (const id of session.themes_available) {
    unique.add(id);
  }
  const ordered = fallback.filter((id) => unique.has(id));
  return ordered.length > 0 ? ordered : fallback;
}

function pickDefaultTheme(session: ViewerSession | null, catalog: ThemeId[]): ThemeId {
  if (session?.theme_default && catalog.includes(session.theme_default)) {
    return session.theme_default;
  }
  return catalog[0] ?? 'neon';
}

function themeLabel(id: string): string {
  return id
    .split('_')
    .join(' ')
    .replace(/\b\w/g, (ch) => ch.toUpperCase());
}

function normalizeColorModeCatalog(session: ViewerSession | null): ColorModeId[] {
  const fallback = listColorModeIds();
  if (!session?.color_modes_available || session.color_modes_available.length === 0) {
    return fallback;
  }

  const unique = new Set<ColorModeId>();
  for (const id of session.color_modes_available) {
    unique.add(id);
  }
  const ordered = fallback.filter((id) => unique.has(id));
  return ordered.length > 0 ? ordered : fallback;
}

function pickDefaultColorMode(session: ViewerSession | null, catalog: ColorModeId[]): ColorModeId {
  if (session?.color_mode_default && catalog.includes(session.color_mode_default)) {
    return session.color_mode_default;
  }
  return catalog[0] ?? 'monochrome_accent';
}

export default function App(): JSX.Element {
  const [session, setSession] = useState<ViewerSession | null>(null);
  const [liteMode] = useState<boolean>(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [themeCatalog, setThemeCatalog] = useState<ThemeId[]>(() => listThemeIds());
  const [activeThemeId, setActiveThemeId] = useState<ThemeId>('neon');
  const [colorModeCatalog, setColorModeCatalog] = useState<ColorModeId[]>(() => listColorModeIds());
  const [activeColorMode, setActiveColorMode] = useState<ColorModeId>('monochrome_accent');
  const fallbackThemes = useMemo<ThemeId[]>(() => listThemeIds(), []);
  const fallbackColorModes = useMemo<ColorModeId[]>(() => listColorModeIds(), []);
  const availableThemes = themeCatalog.length > 0 ? themeCatalog : fallbackThemes;
  const canCycleThemes = availableThemes.length > 1;
  const availableColorModes = colorModeCatalog.length > 0 ? colorModeCatalog : fallbackColorModes;
  const canCycleColorModes = availableColorModes.length > 1;
  const showControls = session?.show_controls ?? true;

  const activeTheme = useMemo(() => themeById(activeThemeId), [activeThemeId]);

  const stageRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const contextRef = useRef<CanvasRenderingContext2D | null>(null);
  const viewportRef = useRef<CanvasViewport>({ cssWidth: 1, cssHeight: 1, dpr: 1 });
  const timelineRef = useRef<ViewerTimelineV1 | null>(null);
  const barsByIdRef = useRef<Map<number, ViewerTimelineBar>>(new Map());
  const currentFrameRef = useRef<ViewerTimelineFrameV1 | null>(null);
  const currentFrameIndexRef = useRef<number>(0);
  const serverPlayheadUsRef = useRef<number>(0);
  const playbackStartPerfMsRef = useRef<number | null>(null);
  const rafIdRef = useRef<number | null>(null);
  const driftStrikeCountRef = useRef<number>(0);

  const sessionRef = useRef<ViewerSession | null>(null);
  const liteModeRef = useRef<boolean>(liteMode);
  const themeRef = useRef(activeTheme);
  const themeRuntimeRef = useRef<Record<string, unknown>>(activeTheme.createRuntimeState());
  const colorModeRef = useRef<ColorModeId>(activeColorMode);

  useEffect(() => {
    sessionRef.current = session;
  }, [session]);

  useEffect(() => {
    liteModeRef.current = liteMode;
    document.body.classList.toggle('lite-mode', liteMode);
    return () => {
      document.body.classList.remove('lite-mode');
    };
  }, [liteMode]);

  useEffect(() => {
    themeRef.current = activeTheme;
    themeRuntimeRef.current = activeTheme.createRuntimeState();
    document.body.dataset.viewerTheme = activeTheme.id;
    return () => {
      delete document.body.dataset.viewerTheme;
    };
  }, [activeTheme]);

  useEffect(() => {
    colorModeRef.current = activeColorMode;
  }, [activeColorMode]);

  const applySessionAnchor = useCallback((nextSession: ViewerSession | null): void => {
    if (!nextSession || typeof nextSession.scheduled_start_unix_ms !== 'number') {
      return;
    }
    playbackStartPerfMsRef.current = scheduledStartToPerfMs(
      nextSession.scheduled_start_unix_ms,
      nextSession.sync_offset_ms ?? 0,
      Date.now(),
      performance.now(),
    );
  }, []);

  const loadInitialData = useCallback(async (): Promise<void> => {
    const [sessionResult, timelineResult, playheadResult] = await Promise.allSettled([
      fetchViewerSession(),
      fetchViewerTimeline(),
      fetchViewerPlayhead(),
    ]);

    let nextNotice: string | null = null;
    const loadedSession = sessionResult.status === 'fulfilled' ? sessionResult.value : null;

    if (sessionResult.status === 'fulfilled') {
      const nextSession = sessionResult.value;
      setSession(nextSession);
      applySessionAnchor(nextSession);
      const catalog = normalizeThemeCatalog(nextSession);
      setThemeCatalog(catalog);
      setActiveThemeId(pickDefaultTheme(nextSession, catalog));
      const colorCatalog = normalizeColorModeCatalog(nextSession);
      setColorModeCatalog(colorCatalog);
      setActiveColorMode(pickDefaultColorMode(nextSession, colorCatalog));
    } else {
      nextNotice = errorMessage(sessionResult.reason, 'Failed to load /api/viewer/session');
      setThemeCatalog(fallbackThemes);
      setActiveThemeId('neon');
      setColorModeCatalog(fallbackColorModes);
      setActiveColorMode('monochrome_accent');
    }

    const motorPlayheadUs = playheadResult.status === 'fulfilled' ? playheadResult.value : 0;
    const syncOffsetMs =
      sessionResult.status === 'fulfilled' && typeof sessionResult.value.sync_offset_ms === 'number'
        ? sessionResult.value.sync_offset_ms
        : 0;
    const offsetUs = toViewerPlayheadUs(motorPlayheadUs, syncOffsetMs);
    serverPlayheadUsRef.current = offsetUs;

    if (timelineResult.status === 'fulfilled') {
      const timeline = timelineResult.value;
      timelineRef.current = timeline;

      const nextBarsById = new Map<number, ViewerTimelineBar>();
      for (const bar of timeline.bars_static) {
        nextBarsById.set(Math.round(bar.id), bar);
      }
      barsByIdRef.current = nextBarsById;

      if (typeof loadedSession?.scheduled_start_unix_ms !== 'number') {
        // Keep the startup latch null until a stable non-zero playhead or an
        // explicit scheduled-start anchor arrives.
        playbackStartPerfMsRef.current = null;
      }

      const frameIndex = clamp(
        Math.floor((offsetUs / 1_000_000) * timeline.fps),
        0,
        timeline.frames.length - 1,
      );
      currentFrameIndexRef.current = frameIndex;
      currentFrameRef.current = timeline.frames[frameIndex] ?? null;
    } else {
      timelineRef.current = null;
      barsByIdRef.current = new Map();
      currentFrameRef.current = null;
      currentFrameIndexRef.current = 0;
      serverPlayheadUsRef.current = 0;
      playbackStartPerfMsRef.current = null;

      const timelineError = errorMessage(timelineResult.reason, 'Failed to load /api/viewer/timeline');
      nextNotice = nextNotice ? `${nextNotice} | ${timelineError}` : timelineError;
    }

    setNotice(nextNotice);
  }, [applySessionAnchor, fallbackColorModes, fallbackThemes]);

  useEffect(() => {
    void loadInitialData();
  }, [loadInitialData]);

  useEffect(() => {
    let canceled = false;
    let pollTimerId: number | null = null;
    let pollInFlight = false;

    const schedulePoll = (): void => {
      if (canceled || !shouldPollForPlaybackStart(playbackStartPerfMsRef.current)) {
        return;
      }
      pollTimerId = window.setTimeout(() => {
        void pollPlayhead();
      }, START_DETECT_POLL_MS);
    };

    const pollPlayhead = async (): Promise<void> => {
      if (canceled || pollInFlight || !shouldPollForPlaybackStart(playbackStartPerfMsRef.current)) {
        return;
      }
      pollInFlight = true;
      try {
        const motorPlayheadUs = await fetchViewerPlayhead();
        if (canceled) {
          return;
        }
        const syncOffsetMs = sessionRef.current?.sync_offset_ms ?? 0;
        const offsetUs = toViewerPlayheadUs(motorPlayheadUs, syncOffsetMs);
        const playbackDetected = hasPlaybackStarted(offsetUs);
        serverPlayheadUsRef.current = offsetUs;
        // Latch the animation clock once when playback begins.
        // Re-anchoring this continuously causes visible timeline jitter.
        if (playbackDetected && playbackStartPerfMsRef.current === null) {
          playbackStartPerfMsRef.current = performance.now() - (offsetUs / 1000);
        }
      } catch {
        // Keep the last good playhead if polling temporarily fails.
      } finally {
        pollInFlight = false;
        schedulePoll();
      }
    };

    void pollPlayhead();

    return () => {
      canceled = true;
      if (pollTimerId !== null) {
        window.clearTimeout(pollTimerId);
      }
    };
  }, []);

  useEffect(() => {
    let canceled = false;
    let timerId: number | null = null;

    const pollSessionForAnchor = async (): Promise<void> => {
      if (canceled || playbackStartPerfMsRef.current !== null) {
        return;
      }
      try {
        const nextSession = await fetchViewerSession();
        if (canceled) {
          return;
        }
        setSession(nextSession);
        applySessionAnchor(nextSession);
      } catch {
        // Keep polling; startup anchor publication is best-effort.
      } finally {
        if (!canceled && playbackStartPerfMsRef.current === null) {
          timerId = window.setTimeout(() => {
            void pollSessionForAnchor();
          }, 50);
        }
      }
    };

    void pollSessionForAnchor();

    return () => {
      canceled = true;
      if (timerId !== null) {
        window.clearTimeout(timerId);
      }
    };
  }, [applySessionAnchor]);

  useEffect(() => {
    let canceled = false;
    let timerId: number | null = null;

    const pollDrift = async (): Promise<void> => {
      try {
        const startMs = playbackStartPerfMsRef.current;
        const timeline = timelineRef.current;
        if (startMs === null || !timeline) {
          return;
        }
        const motorPlayheadUs = await fetchViewerPlayhead();
        if (canceled) {
          return;
        }
        const syncOffsetMs = sessionRef.current?.sync_offset_ms ?? 0;
        const offsetUs = toViewerPlayheadUs(motorPlayheadUs, syncOffsetMs);
        serverPlayheadUsRef.current = offsetUs;
        const localPlayheadUs = Math.max(
          0,
          Math.min(timeline.duration_us, Math.round((performance.now() - startMs) * 1000)),
        );
        const driftThresholdUs = Math.round((sessionRef.current?.drift_rebase_threshold_ms ?? 40) * 1000);
        if (Math.abs(offsetUs - localPlayheadUs) > driftThresholdUs) {
          driftStrikeCountRef.current += 1;
        } else {
          driftStrikeCountRef.current = 0;
        }
        if (driftStrikeCountRef.current >= 3) {
          playbackStartPerfMsRef.current = performance.now() - (offsetUs / 1000);
          driftStrikeCountRef.current = 0;
        }
      } catch {
        // Health-only drift checks should not interrupt rendering.
      } finally {
        if (!canceled) {
          timerId = window.setTimeout(() => {
            void pollDrift();
          }, 250);
        }
      }
    };

    void pollDrift();

    return () => {
      canceled = true;
      if (timerId !== null) {
        window.clearTimeout(timerId);
      }
    };
  }, []);

  const cycleTheme = useCallback((delta: number): void => {
    if (availableThemes.length === 0) {
      return;
    }
    setActiveThemeId((current) => {
      const currentIndex = availableThemes.indexOf(current);
      const baseIndex = currentIndex >= 0 ? currentIndex : 0;
      const nextIndex = (baseIndex + delta + availableThemes.length) % availableThemes.length;
      return availableThemes[nextIndex] ?? current;
    });
  }, [availableThemes]);

  const onThemeChange = useCallback((event: ChangeEvent<HTMLSelectElement>): void => {
    const next = event.target.value as ThemeId;
    if (!availableThemes.includes(next)) {
      return;
    }
    setActiveThemeId(next);
  }, [availableThemes]);

  const cycleColorMode = useCallback((delta: number): void => {
    if (availableColorModes.length === 0) {
      return;
    }
    setActiveColorMode((current) => {
      const currentIndex = availableColorModes.indexOf(current);
      const baseIndex = currentIndex >= 0 ? currentIndex : 0;
      const nextIndex = (baseIndex + delta + availableColorModes.length) % availableColorModes.length;
      return availableColorModes[nextIndex] ?? current;
    });
  }, [availableColorModes]);

  const onColorModeChange = useCallback((event: ChangeEvent<HTMLSelectElement>): void => {
    const next = event.target.value as ColorModeId;
    if (!availableColorModes.includes(next)) {
      return;
    }
    setActiveColorMode(next);
  }, [availableColorModes]);

  useEffect(() => {
    const stage = stageRef.current;
    const canvas = canvasRef.current;
    if (!stage || !canvas) {
      return;
    }

    const resize = (): void => {
      const rect = stage.getBoundingClientRect();
      const cssWidth = Math.max(1, Math.round(rect.width));
      const cssHeight = Math.max(1, Math.round(rect.height));
      const dprCap = liteMode ? Math.min(1.25, themeRef.current.dprCap) : themeRef.current.dprCap;
      const dpr = Math.max(1, Math.min(window.devicePixelRatio || 1, dprCap));

      const nextWidth = Math.max(1, Math.round(cssWidth * dpr));
      const nextHeight = Math.max(1, Math.round(cssHeight * dpr));

      if (canvas.width !== nextWidth || canvas.height !== nextHeight) {
        canvas.width = nextWidth;
        canvas.height = nextHeight;
      }

      viewportRef.current = {
        cssWidth,
        cssHeight,
        dpr,
      };

      if (!contextRef.current) {
        contextRef.current = canvas.getContext('2d', { alpha: false });
      }
    };

    resize();

    const observer = new ResizeObserver(() => {
      resize();
    });
    observer.observe(stage);

    window.addEventListener('orientationchange', resize);

    return () => {
      observer.disconnect();
      window.removeEventListener('orientationchange', resize);
    };
  }, [liteMode, activeTheme.id]);

  useEffect(() => {
    const paint = (ts: number): void => {
      rafIdRef.current = window.requestAnimationFrame(paint);

      const timeline = timelineRef.current;

      let smoothPlayheadUs = 0;
      if (timeline && timeline.frames.length > 0) {
        const serverPlayheadUs = clamp(serverPlayheadUsRef.current, 0, timeline.duration_us);
        const startMs = playbackStartPerfMsRef.current;
        if (startMs === null) {
          smoothPlayheadUs = serverPlayheadUs;
        } else {
          const elapsedUs = Math.max(0, (ts - startMs) * 1000);
          smoothPlayheadUs = clamp(Math.max(serverPlayheadUs, elapsedUs), 0, timeline.duration_us);
        }

        const frameIndex = clamp(Math.floor((smoothPlayheadUs / 1_000_000) * timeline.fps), 0, timeline.frames.length - 1);
        currentFrameIndexRef.current = frameIndex;
        currentFrameRef.current = timeline.frames[frameIndex] ?? null;
      }

      const ctx = contextRef.current;
      if (!ctx) {
        return;
      }

      const physicalViewport = viewportRef.current;
      const logicalViewport = toLogicalViewport(physicalViewport);

      const scene = buildSceneModel({
        session: sessionRef.current,
        timeline: timelineRef.current,
        frame: currentFrameRef.current,
        colorMode: colorModeRef.current,
        smoothPlayheadUs,
        barsById: barsByIdRef.current,
        viewport: logicalViewport,
      });

      ctx.setTransform(physicalViewport.dpr, 0, 0, physicalViewport.dpr, 0, 0);
      ctx.clearRect(0, 0, physicalViewport.cssWidth, physicalViewport.cssHeight);
      applyVerticalSceneTransform(ctx, physicalViewport);

      themeRef.current.draw({
        ctx,
        viewport: logicalViewport,
        physicalViewport,
        scene,
        liteMode: liteModeRef.current,
        runtime: themeRuntimeRef.current,
        drawUprightText: createUprightTextDrawer(ctx, physicalViewport),
      });
    };

    rafIdRef.current = window.requestAnimationFrame(paint);

    return () => {
      if (rafIdRef.current !== null) {
        window.cancelAnimationFrame(rafIdRef.current);
        rafIdRef.current = null;
      }
    };
  }, []);

  return (
    <div className="viewer-root viewer-fullscreen" data-theme={activeTheme.id}>
      <section className="viewer-stage" ref={stageRef}>
        {showControls && (
          <div className="switcher-stack">
            <div className="theme-switcher" role="group" aria-label="Theme switcher">
              <span className="theme-label">Theme</span>
              <button
                type="button"
                className="theme-nav"
                onClick={() => cycleTheme(-1)}
                aria-label="Previous theme"
                disabled={!canCycleThemes}
              >
                {'<'}
              </button>
              <select className="theme-select" value={activeThemeId} onChange={onThemeChange} aria-label="Select theme">
                {availableThemes.map((id) => (
                  <option key={id} value={id}>
                    {themeLabel(id)}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="theme-nav"
                onClick={() => cycleTheme(1)}
                aria-label="Next theme"
                disabled={!canCycleThemes}
              >
                {'>'}
              </button>
            </div>
            <div className="theme-switcher" role="group" aria-label="Color mode switcher">
              <span className="theme-label">Color</span>
              <button
                type="button"
                className="theme-nav"
                onClick={() => cycleColorMode(-1)}
                aria-label="Previous color mode"
                disabled={!canCycleColorModes}
              >
                {'<'}
              </button>
              <select className="theme-select" value={activeColorMode} onChange={onColorModeChange} aria-label="Select color mode">
                {availableColorModes.map((id) => (
                  <option key={id} value={id}>
                    {themeLabel(id)}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="theme-nav"
                onClick={() => cycleColorMode(1)}
                aria-label="Next color mode"
                disabled={!canCycleColorModes}
              >
                {'>'}
              </button>
            </div>
          </div>
        )}
        <canvas ref={canvasRef} aria-label="Scrolling MIDI viewer" />

        {notice && <div className="stage-notice">{notice}</div>}
      </section>
    </div>
  );
}
