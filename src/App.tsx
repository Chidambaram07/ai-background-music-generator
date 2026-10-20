import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE_URL, ApiError } from "./services/api";
import { audioUrl, cancelMusicJob, createLoopJob, createMusicJob, createVoiceoverDuckingJob, downloadJobPackage, duckedAudioUrl, generateMusicFromSpecification, getLoopValidation, getMusicJob, getMusicStems, getMusicValidation, getPs1Validation, loopAudioUrl, ps1ValidationReportUrl, regenerateStem, runPs1Validation, type MusicJob, type Ps1ValidationReport, type ValidationRequirement, uploadVoiceoverFile, waitForMusicJob } from "./services/musicApi";
import { createMusicSpecification, type MusicSpecification } from "./services/specificationApi";
import { evidenceStatus, measured, numeric, qualityRows, record, text, validationEvidence, yesNo, type QcStatus } from "./services/validation";

type Page =
  | "create"
  | "specification"
  | "progress"
  | "studio"
  | "jobs"
  | "projects"
  | "validation"
  | "testlab"
  | "settings"
  | "system";

type ButtonProps = {
  children: React.ReactNode;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  className?: string;
  disabled?: boolean;
  onClick?: () => void;
};

const Button = ({
  children,
  variant = "secondary",
  className = "",
  disabled,
  onClick,
}: ButtonProps) => (
  <button
    className={`button button--${variant} ${className}`}
    disabled={disabled}
    onClick={onClick}
  >
    {children}
  </button>
);

const IconButton = ({
  label,
  children,
  active,
  onClick,
}: {
  label: string;
  children: React.ReactNode;
  active?: boolean;
  onClick?: () => void;
}) => (
  <button
    aria-label={label}
    title={label}
    className={`icon-button ${active ? "is-active" : ""}`}
    onClick={onClick}
  >
    {children}
  </button>
);

const Badge = ({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "success" | "warning" | "danger" | "accent" | "info";
}) => <span className={`badge badge--${tone}`}>{children}</span>;

const Card = ({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) => <section className={`card ${className}`}>{children}</section>;

const SectionHeader = ({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: string;
  action?: React.ReactNode;
}) => (
  <div className="section-header">
    <div>
      <div className="section-title">{title}</div>
      {detail && <div className="section-detail">{detail}</div>}
    </div>
    {action}
  </div>
);

const Toggle = ({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange?: () => void;
}) => (
  <button
    aria-label={checked ? "Turn off" : "Turn on"}
    className={`toggle ${checked ? "is-on" : ""}`}
    onClick={onChange}
  >
    <span />
  </button>
);

const Waveform = ({
  compact = false,
  muted = false,
  color = "violet",
}: {
  compact?: boolean;
  muted?: boolean;
  color?: "violet" | "blue" | "teal" | "amber";
}) => {
  const bars = useMemo(
    () =>
      Array.from({ length: compact ? 54 : 104 }, (_, index) => {
        const base = Math.abs(
          Math.sin(index * 1.77) * 0.58 + Math.cos(index * 0.43) * 0.28,
        );
        return Math.max(12, Math.round(base * 88));
      }),
    [compact],
  );

  return (
    <div
      className={`waveform waveform--${color} ${compact ? "waveform--compact" : ""} ${muted ? "is-muted" : ""}`}
    >
      {bars.map((height, index) => (
        <i key={index} style={{ height: `${height}%` }} />
      ))}
    </div>
  );
};

function formatAudioTime(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const total = Math.floor(seconds);
  const minutes = Math.floor(total / 60);
  const secs = total % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function formatDateTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

type CreateMusicSubmission = {
  prompt: string;
  durationSeconds: number;
  genre: string;
  mood: string[];
  tempo: number;
  key: string;
  instruments: string[];
  sections: MusicSpecification["sections"];
  ducking?: boolean;
  voiceoverPath?: string;
  looping?: boolean;
};

function buildDefaultSections(durationSeconds: number, mood: string) {
  const safeDuration = Math.max(5, Number.isFinite(durationSeconds) ? durationSeconds : 45);
  const ratios = [0.22, 0.33, 0.33, 0.12];
  const labels = ["INTRO", "BUILD", "MAIN", "OUTRO"];
  const normalizedMood = mood.trim() || "Calm";
  const rawEnds: number[] = [];
  let accumulated = 0;
  ratios.forEach((ratio, index) => {
    accumulated += ratio;
    rawEnds.push(index === ratios.length - 1 ? safeDuration : Number((safeDuration * accumulated).toFixed(2)));
  });

  let start = 0;
  return labels.map((name, index) => {
    const end = index === rawEnds.length - 1 ? safeDuration : Math.max(start + 0.01, rawEnds[index]);
    const section = {
      name,
      start: Number(start.toFixed(2)),
      end: Number(end.toFixed(2)),
      mood: normalizedMood,
    };
    start = end;
    return section;
  });
}

export function AudioPlayer({
  src,
  label,
  downloadName,
  audioRef,
  muted = false,
  solo = false,
  onSolo,
  onMute,
  onProgress,
  resetToken = 0,
  loopRange,
}: {
  src: string;
  label: string;
  downloadName?: string;
  audioRef?: (element: HTMLAudioElement | null) => void;
  muted?: boolean;
  solo?: boolean;
  onSolo?: () => void;
  onMute?: () => void;
  onProgress?: (currentTime: number, duration: number) => void;
  resetToken?: number;
  loopRange?: { start: number; end: number } | null;
}) {
  const internalRef = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState("");
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const activeLoopRange = loopRange && loopRange.end > loopRange.start ? loopRange : null;

  const setRef = (element: HTMLAudioElement | null) => {
    internalRef.current = element;
    audioRef?.(element);
  };

  useEffect(() => {
    const audio = internalRef.current;
    if (!audio) return;
    audio.muted = muted;
  }, [muted]);

  useEffect(() => {
    const audio = internalRef.current;
    if (!audio) return;
    audio.volume = volume;
  }, [volume]);

  useEffect(() => {
    const audio = internalRef.current;
    console.log("[AudioPlayer] src change", { label, src, currentSrc: audio?.currentSrc || null });
    if (!audio) return;
    audio.pause();
    audio.currentTime = 0;
    setPlaying(false);
    setCurrentTime(0);
    setDuration(0);
    setError("");
    if (src) {
      console.log("[AudioPlayer] loading", { label, src });
      audio.load();
    }
  }, [src, label]);

  useEffect(() => {
    if (resetToken === 0) return;
    const audio = internalRef.current;
    if (!audio) return;
    audio.pause();
    audio.currentTime = 0;
    audio.muted = false;
    audio.volume = 1;
    setPlaying(false);
    setCurrentTime(0);
    setVolume(1);
    setError("");
  }, [resetToken]);

  const toggle = async () => {
    const audio = internalRef.current;
    if (!audio || !src) return;
    console.log("[AudioPlayer] toggle", { label, src, paused: audio.paused, currentSrc: audio.currentSrc });
    setError("");
    try {
      if (audio.paused) {
        if (activeLoopRange && (audio.currentTime < activeLoopRange.start || audio.currentTime >= activeLoopRange.end)) {
          audio.currentTime = activeLoopRange.start;
          setCurrentTime(activeLoopRange.start);
        }
        console.log("[AudioPlayer] play()", { label, src });
        await audio.play();
      } else {
        console.log("[AudioPlayer] pause()", { label, src });
        audio.pause();
      }
    } catch (error) {
      console.log("[AudioPlayer] play error", { label, src, error });
      setPlaying(false);
      setError(`Unable to play ${label}. Use Download to verify the WAV file.`);
    }
  };

  const visibleCurrentTime = activeLoopRange
    ? Math.min(activeLoopRange.end, Math.max(activeLoopRange.start, currentTime))
    : currentTime;
  const visibleDuration = activeLoopRange ? activeLoopRange.end : duration;
  const progress = activeLoopRange
    ? Math.min(100, Math.max(0, ((visibleCurrentTime - activeLoopRange.start) / (activeLoopRange.end - activeLoopRange.start)) * 100))
    : duration > 0
      ? Math.min(100, Math.max(0, (currentTime / duration) * 100))
      : 0;

  return (
    <div className="audio-player" style={{ width: "100%", minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <button className="play-button" type="button" onClick={() => void toggle()} disabled={!src} aria-label={playing ? `Pause ${label}` : `Play ${label}`}>
          {playing ? "Ⅱ" : "▶"}
        </button>
        <button className={`icon-button ${muted ? "is-active" : ""}`} type="button" onClick={onMute} aria-label={`${muted ? "Unmute" : "Mute"} ${label}`} title={muted ? "Unmute" : "Mute"}>
          {muted ? "M" : "🔊"}
        </button>
        <button className={`icon-button ${solo ? "is-active" : ""}`} type="button" onClick={onSolo} aria-label={`${solo ? "Unsolo" : "Solo"} ${label}`} title={solo ? "Unsolo" : "Solo"}>S
        </button>
      </div>

      <audio
        ref={setRef}
        preload="metadata"
        src={src || undefined}
        style={{ width: "100%", display: "block" }}
        onLoadedMetadata={(event) => {
          console.log("[AudioPlayer] loaded metadata", { label, src, duration: event.currentTarget.duration, currentSrc: event.currentTarget.currentSrc });
          const value = event.currentTarget.duration;
          if (Number.isFinite(value)) {
            setDuration(value);
            if (activeLoopRange && (event.currentTarget.currentTime < activeLoopRange.start || event.currentTarget.currentTime >= activeLoopRange.end)) {
              event.currentTarget.currentTime = activeLoopRange.start;
              setCurrentTime(activeLoopRange.start);
              onProgress?.(activeLoopRange.start, value);
              return;
            }
            onProgress?.(event.currentTarget.currentTime, value);
          }
        }}
        onDurationChange={(event) => {
          const value = event.currentTarget.duration;
          if (Number.isFinite(value)) { setDuration(value); onProgress?.(event.currentTarget.currentTime, value); }
        }}
        onTimeUpdate={(event) => {
          const audio = event.currentTarget;
          const value = audio.currentTime;
          if (activeLoopRange && value >= activeLoopRange.end) {
            audio.currentTime = activeLoopRange.start;
            setCurrentTime(activeLoopRange.start);
            onProgress?.(activeLoopRange.start, audio.duration);
            return;
          }
          setCurrentTime(value);
          onProgress?.(value, audio.duration);
        }}
        onPlay={(event) => { console.log("[AudioPlayer] play event", { label, src, currentSrc: event.currentTarget.currentSrc }); setPlaying(true); setError(""); }}
        onPause={() => setPlaying(false)}
        onEnded={() => {
          if (activeLoopRange) {
            const audio = internalRef.current;
            if (audio) {
              audio.currentTime = activeLoopRange.start;
              setCurrentTime(activeLoopRange.start);
              onProgress?.(activeLoopRange.start, duration || audio.duration);
              void audio.play().catch(() => {
                setPlaying(false);
                setError(`Unable to play ${label}. Use Download to verify the WAV file.`);
              });
              setPlaying(true);
              return;
            }
          }
          setPlaying(false); setCurrentTime(0); onProgress?.(0, duration); 
        }}
        onError={(event) => {
          const mediaError = event.currentTarget.error;
          console.log("[AudioPlayer] media error", { label, src, code: mediaError?.code, message: mediaError?.message, currentSrc: event.currentTarget.currentSrc, networkState: event.currentTarget.networkState, readyState: event.currentTarget.readyState });
          setPlaying(false);
          setError(`The ${label} WAV could not be loaded by the browser.`);
        }}
      />

      <div style={{ display: "flex", alignItems: "center", gap: 7, marginTop: 4 }}>
        <span style={{ fontSize: 9, opacity: 0.65, minWidth: 28 }}>{formatAudioTime(visibleCurrentTime)}</span>
        <input aria-label={`${label} volume`} type="range" min="0" max="1" step="0.01" value={volume} onChange={(event) => setVolume(Number(event.target.value))} style={{ flex: 1 }} />
        <span style={{ fontSize: 9, opacity: 0.65, minWidth: 28, textAlign: "right" }}>{Math.round(volume * 100)}%</span>
        <span style={{ fontSize: 9, opacity: 0.65, minWidth: 35, textAlign: "right" }}>{visibleDuration ? formatAudioTime(visibleDuration) : "--:--"}</span>
      </div>

      <div aria-hidden="true" style={{ height: 3, marginTop: 4, borderRadius: 999, background: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
        <div style={{ height: "100%", width: `${progress}%`, borderRadius: 999, background: "currentColor", transition: "width 80ms linear" }} />
      </div>

      {activeLoopRange && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginTop: 6, fontSize: 9, color: "var(--muted)" }}>
          <span>Seamless looping active</span>
          <span>{formatAudioTime(activeLoopRange.start)} → {formatAudioTime(activeLoopRange.end)}</span>
        </div>
      )}

      {src && <a className="button" href={src} download={downloadName || undefined} target="_blank" rel="noreferrer" style={{ marginTop: 5 }}>⇩ Download</a>}
      {error && <small style={{ display: "block", marginTop: 4, color: "#ff8b8b" }}>{error}</small>}
    </div>
  );
}

const navGroups = [
  {
    label: "CREATE",
    items: [
      { id: "create" as Page, icon: "✦", label: "Create Music" },
      { id: "specification" as Page, icon: "◇", label: "Music Specification" },
    ],
  },
  {
    label: "WORKSPACE",
    items: [
      { id: "studio" as Page, icon: "◉", label: "Studio" },
      { id: "jobs" as Page, icon: "◷", label: "Jobs" },
      { id: "projects" as Page, icon: "▣", label: "Projects" },
    ],
  },
  {
    label: "QUALITY",
    items: [
      { id: "validation" as Page, icon: "✓", label: "Validation" },
      { id: "testlab" as Page, icon: "◇", label: "Test Lab" },
    ],
  },
  {
    label: "SYSTEM",
    items: [
      { id: "settings" as Page, icon: "⚙", label: "Settings" },
      { id: "system" as Page, icon: "▤", label: "Design System" },
    ],
  },
];

const pageTitles: Record<Page, string> = {
  create: "Create Music",
  specification: "Music Specification",
  progress: "Generation Job",
  studio: "Studio",
  jobs: "Generation Jobs",
  projects: "Projects",
  validation: "PS1 Validation",
  testlab: "Test Lab",
  settings: "Settings",
  system: "Design System",
};

function Shell({
  page,
  setPage,
  children,
}: {
  page: Page;
  setPage: (page: Page) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand" onClick={() => setPage("create")}>
          <div className="brand-mark"><span /><span /><span /></div>
          <div className="brand-copy">
            <strong>IN CUBRIX</strong>
            <small>MUSIC ENGINE</small>
          </div>
        </div>
        <nav className="nav">
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <div className="nav-label">{group.label}</div>
              {group.items.map((item) => (
                <button
                  className={`nav-item ${page === item.id ? "is-active" : ""}`}
                  key={item.id}
                  onClick={() => setPage(item.id)}
                >
                  <span className="nav-icon">{item.icon}</span>
                  {item.label}
                  {item.id === "jobs" && <span className="nav-count">3</span>}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="api-status">
            <span className="status-dot" />
            <div><strong>API Connected</strong><small>Production · 38ms</small></div>
          </div>
        </div>
      </aside>
      <div className="main-shell">
        <header className="topbar">
          <div className="topbar-title">{pageTitles[page]}</div>
          <div className="topbar-actions">
            <button className="command">
              <span>⌕</span>
              Search or run a command
              <kbd>⌘ K</kbd>
            </button>
            <Badge tone="success"><span className="mini-dot" /> All systems operational</Badge>
            <IconButton label="Help">?</IconButton>
            <button className="avatar">AK</button>
          </div>
        </header>
        <main className="content">{children}</main>
      </div>
    </div>
  );
}

function CreateMusic({ onGenerate, submitting, error }: { onGenerate: (request: CreateMusicSubmission) => void; submitting: boolean; error: string }) {
  const [prompt, setPrompt] = useState(
    "Warm cinematic background music for a technology product video. Start calm and gradually become more energetic. No vocals.",
  );
  const [durationChoice, setDurationChoice] = useState("45 sec");
  const [customDurationSeconds, setCustomDurationSeconds] = useState(45);
  const [mood, setMood] = useState("Inspiring");
  const [genre, setGenre] = useState("Cinematic");
  const [tempo, setTempo] = useState(100);
  const [keySignature, setKeySignature] = useState("C major");
  const [instruments, setInstruments] = useState(["Drums", "Bass", "Piano", "Pads"]);
  const [ducking, setDucking] = useState(false);
  const [voiceoverPath, setVoiceoverPath] = useState("");
  const [voiceoverFilename, setVoiceoverFilename] = useState("");
  const [voiceoverUploading, setVoiceoverUploading] = useState(false);
  const [voiceoverError, setVoiceoverError] = useState("");
  const [looping, setLooping] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const durationSeconds = durationChoice === "Custom" ? customDurationSeconds : Number.parseFloat(durationChoice);
  const sections = useMemo(
    () => buildDefaultSections(durationSeconds, mood),
    [durationSeconds, mood],
  );

  const handleVoiceoverSelection = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      setVoiceoverUploading(true);
      setVoiceoverError("");
      const response = await uploadVoiceoverFile(file);
      setVoiceoverFilename(response.filename);
      setVoiceoverPath(response.path);
    } catch (uploadError) {
      setVoiceoverError(uploadError instanceof ApiError ? uploadError.message : "Unable to upload the selected voiceover file.");
      setVoiceoverPath("");
      setVoiceoverFilename("");
    } finally {
      setVoiceoverUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
    }
  };

  const instrumentData = [
    ["Drums", "DR", "Rhythm & impact"],
    ["Bass", "BS", "Low-end foundation"],
    ["Piano", "PN", "Harmonic movement"],
    ["Pads", "PD", "Atmosphere & depth"],
    ["Strings", "ST", "Emotional texture"],
    ["Synth", "SY", "Modern tonal layer"],
    ["Guitar", "GT", "Organic rhythm"],
    ["Brass", "BR", "Power & emphasis"],
    ["Percussion", "PC", "Secondary pulse"],
  ];

  const toggleInstrument = (name: string) =>
    setInstruments((current) =>
      current.includes(name)
        ? current.filter((instrument) => instrument !== name)
        : [...current, name],
    );

  return (
    <div className="page page--create">
      <div className="page-heading">
        <div>
          <div className="eyebrow">AI MUSIC COMPOSER</div>
          <div className="page-title">Create Background Music</div>
          <div className="page-subtitle">
            Describe the music you need and configure the generation.
          </div>
        </div>
        <div className="draft-state"><span>✓</span> Draft saved just now</div>
      </div>

      <Card className="prompt-card">
        <SectionHeader
          title="Describe your music"
          detail="Use natural language to define the sound, pacing, and creative intent."
          action={<Badge tone="accent">PRIMARY INPUT</Badge>}
        />
        <div className="prompt-editor">
          <textarea
            value={prompt}
            onChange={(event) => setPrompt(event.target.value)}
            placeholder="Describe the background music you want…"
          />
          <div className="prompt-toolbar">
            <span>{prompt.length} / 1,000</span>
            <div>
              <Button variant="ghost" onClick={() => setPrompt("")}>Clear</Button>
              <Button variant="secondary"><span className="spark">✦</span> Enhance prompt</Button>
            </div>
          </div>
        </div>
        <div className="examples">
          <span>Try an example</span>
          {["Corporate", "Cinematic", "Documentary", "Technology", "Peaceful", "Energetic", "Lo-fi"].map(
            (item) => <button key={item} onClick={() => setPrompt(`${item} background music with a polished, modern progression and no vocals.`)}>{item}</button>,
          )}
        </div>
      </Card>

      <div className="section-block">
        <SectionHeader
          title="Music Configuration"
          detail="Shape the timing and musical character."
        />
        <div className="config-grid">
          <Card className="config-card config-card--wide">
            <div className="field-label">DURATION <span>{durationSeconds.toFixed(durationSeconds % 1 === 0 ? 0 : 2)} seconds</span></div>
            <div className="segmented duration-options">
              {["10 sec", "30 sec", "45 sec", "60 sec", "90 sec", "180 sec", "Custom"].map((item) => (
                <button
                  className={durationChoice === item ? "is-selected" : ""}
                  key={item}
                  onClick={() => {
                    setDurationChoice(item);
                    if (item !== "Custom") {
                      setCustomDurationSeconds(Number.parseFloat(item));
                    }
                  }}
                >{item}</button>
              ))}
            </div>
            {durationChoice === "Custom" && (
              <label style={{ display: "grid", gap: 8, marginTop: 12 }}>
                <span className="field-label">CUSTOM DURATION</span>
                <input
                  aria-label="Custom duration seconds"
                  type="number"
                  min="5"
                  max="300"
                  step="1"
                  value={customDurationSeconds}
                  onChange={(event) => setCustomDurationSeconds(Math.max(5, Math.min(300, Number(event.target.value) || 5)))}
                />
              </label>
            )}
          </Card>
          <Card className="config-card">
            <div className="field-label">MOOD</div>
            <div className="chip-wrap">
              {["Calm", "Hopeful", "Energetic", "Dark", "Peaceful", "Tense", "Inspiring"].map((item) => (
                <button className={mood === item ? "chip is-selected" : "chip"} key={item} onClick={() => setMood(item)}>{item}</button>
              ))}
            </div>
          </Card>
          <Card className="config-card">
            <div className="field-label">GENRE</div>
            <div className="chip-wrap">
              {["Cinematic", "Corporate", "Ambient", "Electronic", "Lo-fi", "Documentary"].map((item) => (
                <button className={genre === item ? "chip is-selected" : "chip"} key={item} onClick={() => setGenre(item)}>{item}</button>
              ))}
            </div>
          </Card>
          <Card className="config-card">
            <div className="field-row">
              <div>
                <div className="field-label">TEMPO</div>
                <label className="select-like" style={{ display: "grid", gap: 4 }}>
                  <span>Explicit input</span>
                  <input
                    aria-label="Tempo BPM"
                    type="number"
                    min="40"
                    max="240"
                    step="1"
                    value={tempo}
                    onChange={(event) => setTempo(Math.max(40, Math.min(240, Number(event.target.value) || 40)))}
                  />
                </label>
              </div>
              <div>
                <div className="field-label">KEY</div>
                <label className="select-like" style={{ display: "grid", gap: 4 }}>
                  <span>Explicit input</span>
                  <input
                    aria-label="Musical key"
                    type="text"
                    value={keySignature}
                    onChange={(event) => setKeySignature(event.target.value)}
                    placeholder="C major"
                  />
                </label>
              </div>
            </div>
          </Card>
        </div>
      </div>

      <Card className="instruments-card">
        <SectionHeader
          title="Instrument Stems"
          detail="Choose the instruments you want represented in the generated specification."
          action={<Badge tone="info">{instruments.length} SELECTED</Badge>}
        />
        <div className="instrument-grid">
          {instrumentData.map(([name, short, description]) => {
            const selected = instruments.includes(name);
            return (
              <button
                className={`instrument ${selected ? "is-selected" : ""}`}
                key={name}
                onClick={() => toggleInstrument(name)}
              >
                <span className="instrument-icon">{short}</span>
                <span><strong>{name}</strong><small>{description}</small></span>
                <i>{selected ? "✓" : "+"}</i>
              </button>
            );
          })}
        </div>
      </Card>

      <Card className="structure-card">
        <SectionHeader
          title="Music Structure"
          detail="Define creative changes across your video timeline."
          action={<Badge tone="info">AUTO FROM DURATION</Badge>}
        />
        <div className="timeline-ruler">
          {[0, ...sections.map((section) => section.end)].map((time, index) => <span key={`${time}-${index}`}>{formatAudioTime(time)}</span>)}
        </div>
        <div className="section-timeline">
          {sections.map((section, index) => {
            const width = Math.max(8, ((section.end - section.start) / Math.max(durationSeconds, 1)) * 100);
            return (
            <button className={`timeline-section timeline-section--${index + 1}`} style={{ width: `${width}%` }} key={section.name ?? `section-${index}`}>
              <strong>{section.name ?? `SECTION ${index + 1}`}</strong><span>{formatAudioTime(section.start)}–{formatAudioTime(section.end)}</span><small>{section.mood}</small>
            </button>
          );})}
        </div>
        <div className="timeline-note"><span>✦</span> Section boundaries will be shared across every generated stem.</div>
      </Card>

      <div className="optional-grid">
        <Card className="optional-card">
          <div className="optional-heading">
            <div className="optional-icon">VO</div>
            <div><strong>Voiceover Ducking</strong><small>Lower music automatically during speech.</small></div>
            <Toggle checked={ducking} onChange={() => setDucking(!ducking)} />
          </div>
          {ducking ? (
            <div className="ducking-content">
              <input ref={fileInputRef} type="file" accept=".wav,.mp3,.m4a,.aac,.flac" hidden onChange={handleVoiceoverSelection} />
              <div className="upload-zone">
                <span>↑</span>
                <strong>{voiceoverFilename ? "Voiceover selected" : "Drop voiceover audio here"}</strong>
                <small>{voiceoverFilename || "WAV, MP3, M4A · up to 500 MB"}</small>
                <Button onClick={() => fileInputRef.current?.click()} disabled={voiceoverUploading}>{voiceoverUploading ? "Uploading…" : "Browse Files"}</Button>
              </div>
              {voiceoverError && <div className="error-text">{voiceoverError}</div>}
              {voiceoverPath && <div className="mini-setting"><span>Uploaded voiceover</span><strong>{voiceoverFilename}</strong></div>}
              <div className="mini-setting"><span>Automatic speech detection</span><Toggle checked /></div>
              <div className="mini-setting"><span>Ducking amount</span><strong>−12 dB</strong></div>
            </div>
          ) : <div className="optional-preview"><span className="voice-line" /><span className="music-line" /></div>}
        </Card>
        <Card className="optional-card">
          <div className="optional-heading">
            <div className="optional-icon">LP</div>
            <div><strong>Seamless Looping</strong><small>Create a continuous, gapless loop.</small></div>
            <Toggle checked={looping} onChange={() => setLooping(!looping)} />
          </div>
          <div className={`loop-preview ${looping ? "is-active" : ""}`}>
            <span>END</span><div><i /><i /><i /><i /><i /><i /><i /></div><b>↻</b><div><i /><i /><i /><i /><i /><i /><i /></div><span>START</span>
          </div>
        </Card>
      </div>

      <Card className="advanced-card">
        <button className="accordion-trigger" onClick={() => setAdvanced(!advanced)}>
          <div><strong>Advanced Options</strong><small>Seed, model preferences, output format, sample rate</small></div>
          <span>{advanced ? "−" : "+"}</span>
        </button>
        {advanced && (
          <div className="advanced-grid">
            <label><span>Seed</span><input defaultValue="4281097" /></label>
            <label><span>Output format</span><select defaultValue="wav"><option value="wav">WAV · 24-bit</option><option value="mp3">MP3 · 320 kbps</option></select></label>
            <label><span>Sample rate</span><select defaultValue="48"><option value="48">48,000 Hz</option><option value="44">44,100 Hz</option></select></label>
          </div>
        )}
      </Card>

      <div className="generate-footer">
        <div><strong>{error ? "Generation request could not be submitted" : submitting ? "Submitting generation request…" : "Ready to generate"}</strong><span>{error || `${instruments.length} selected instruments · ${durationSeconds} sec · ${tempo} BPM · ${keySignature}`}</span></div>
        <div className="generate-action">
          <Button
            variant="primary"
            disabled={submitting || !prompt.trim() || instruments.length === 0}
            onClick={() => onGenerate({
              prompt,
              durationSeconds,
              genre,
              mood: [mood],
              tempo,
              key: keySignature,
              instruments,
              sections,
              ducking,
              voiceoverPath,
              looping,
            })}
          ><span>✦</span> {submitting ? "Creating Job…" : "Generate Background Music"}</Button>
          <small>Runs asynchronously — you can continue working while it processes.</small>
        </div>
      </div>
    </div>
  );
}

function MusicSpecificationPage({ onGenerate }: { onGenerate: (specification: MusicSpecification) => void }) {
  const [prompt, setPrompt] = useState(
    "I want peaceful music for a nature documentary, gradually becoming hopeful.",
  );
  const [specification, setSpecification] = useState<MusicSpecification | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const generateSpecification = async () => {
    const trimmed = prompt.trim();
    if (!trimmed) {
      setError("Please enter a music prompt.");
      return;
    }
    setLoading(true);
    setError("");
    setSpecification(null);
    try {
      const response = await createMusicSpecification({ prompt: trimmed });
      setSpecification(response.specification);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Unable to create the music specification.");
    } finally {
      setLoading(false);
    }
  };

  const examples = [
    "I want peaceful music for a nature documentary, gradually becoming hopeful.",
    "Create energetic electronic music for a product launch.",
    "I need sad piano music for a short emotional film.",
    "Make relaxing ambient music for meditation.",
  ];

  return (
    <div className="page specification-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">MODULE 2 · LLM ORCHESTRATION</div>
          <div className="page-title">Prompt → Music Specification</div>
          <div className="page-subtitle">Convert natural language into validated JSON for the deterministic music engine.</div>
        </div>
        <Badge tone="accent">STRUCTURED OUTPUT</Badge>
      </div>

      <div className="spec-pipeline">
        {[
          ["01", "Natural language", "User prompt"],
          ["02", "LLM", "Interpretation"],
          ["03", "Structured JSON", "Music specification"],
          ["04", "Validation", "Strict schema"],
          ["05", "Music engine", "Downstream input"],
        ].map(([number, title, detail], index) => (
          <div className="spec-pipeline-step" key={number}>
            <span>{number}</span><div><strong>{title}</strong><small>{detail}</small></div>
            {index < 4 && <b>→</b>}
          </div>
        ))}
      </div>

      <Card className="spec-prompt-card">
        <SectionHeader
          title="Describe the music"
          detail="The LLM only translates intent into a structured specification. It never generates audio."
          action={<Badge tone="info">POST /api/v1/specifications</Badge>}
        />
        <div className="spec-prompt-editor">
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={1000} placeholder="Describe the music you need…" />
          <div className="spec-prompt-footer">
            <span>{prompt.length} / 1,000</span>
            <div>
              <Button variant="ghost" onClick={() => setPrompt("")} disabled={loading}>Clear</Button>
              <Button variant="primary" onClick={() => void generateSpecification()} disabled={loading || !prompt.trim()}>
                ✦ {loading ? "Building Specification…" : "Generate Specification"}
              </Button>
            </div>
          </div>
        </div>
        <div className="spec-examples">
          <span>Test prompts</span>
          {examples.map((example, index) => (
            <button key={example} onClick={() => setPrompt(example)}>{`Test ${index + 1}`}</button>
          ))}
        </div>
      </Card>

      {error && (
        <div className="spec-error"><span>!</span><div><strong>Specification request failed</strong><small>{error}</small></div></div>
      )}

      {specification ? (
        <div className="spec-result-layout">
          <div className="spec-result-main">
            <Card className="spec-card">
              <SectionHeader title="Validated Music Specification" detail="This is the only representation that should be passed to the downstream music engine." action={<Badge tone="success">VALIDATED</Badge>} />
              <div className="spec-metrics">
                <div><span>GENRE</span><strong>{specification.genre}</strong></div>
                <div><span>TEMPO</span><strong>{specification.tempo} BPM</strong></div>
                <div><span>KEY</span><strong>{specification.key}</strong></div>
                <div><span>DURATION</span><strong>{specification.sections[specification.sections.length - 1]?.end ?? 0}s</strong></div>
              </div>
              <div className="spec-field-block"><span>MOOD</span><div className="chip-wrap">{specification.mood.map((item) => <span className="chip is-selected" key={item}>{item}</span>)}</div></div>
              <div className="spec-field-block"><span>INSTRUMENTS</span><div className="spec-instrument-list">{specification.instruments.map((item) => <span key={item}>♪ {item}</span>)}</div></div>
              <div className="spec-field-block"><span>MOOD TIMELINE</span><div className="spec-timeline-result">{specification.sections.map((section, index) => { const max = specification.sections[specification.sections.length - 1]?.end || 1; const width = Math.max(5, ((section.end - section.start) / max) * 100); return <div key={`${section.start}-${section.end}-${index}`} style={{ width: `${width}%` }}><strong>{section.mood}</strong><small>{section.start}s – {section.end}s</small></div>; })}</div></div>
            </Card>
          </div>
          <Card className="spec-json-card">
            <SectionHeader title="JSON Output" detail="Validated payload" action={<Badge tone="success">READY</Badge>} />
            <pre>{JSON.stringify(specification, null, 2)}</pre>
            <div style={{ marginTop: 16 }}>
              <Button variant="primary" onClick={() => onGenerate(specification)}>✦ Generate Music from Specification</Button>
              <small style={{ display: "block", marginTop: 8, opacity: 0.7 }}>Module 3 consumes this validated JSON directly — no second LLM call.</small>
            </div>
          </Card>
        </div>
      ) : (
        <Card className="spec-empty">
          <div className="spec-empty-icon">◇</div>
          <strong>No specification generated yet</strong>
          <small>Enter a natural-language request above and generate a validated music specification.</small>
        </Card>
      )}
    </div>
  );
}

function GenerationProgress({ job, onComplete, onCancel }: { job: MusicJob | null; onComplete: () => void; onCancel: () => void }) {
  const [cancelError, setCancelError] = useState("");
  const [cancelling, setCancelling] = useState(false);

  const handleCancel = async () => {
    if (!job || job.status === "succeeded" || job.status === "failed" || job.status === "cancelled") return;
    setCancelling(true);
    setCancelError("");
    try {
      await cancelMusicJob(job.job_id);
      onCancel();
    } catch (error) {
      setCancelError(error instanceof ApiError ? error.message : "Unable to cancel the generation job.");
    } finally {
      setCancelling(false);
    }
  };

  const progress = job?.progress ?? 0;
  const status = job?.status ?? "queued";
  const generatedStems = job?.result && typeof job.result === "object" && "stems" in job.result
    ? (job.result.stems as Array<{ stem_id?: string; name: string }>)
    : [];
  const statusLabel = status === "queued" ? "QUEUED" : status === "processing" ? "PROCESSING" : status === "succeeded" ? "SUCCEEDED" : status === "failed" ? "FAILED" : "CANCELLED";
  const steps = [
    ["Understanding prompt", progress >= 10, progress >= 1 && progress < 25],
    ["Creating music specification", progress >= 25, progress >= 25 && progress < 40],
    ["Generating stems", progress >= 40, progress >= 40 && progress < 75],
    ["Mixing audio", progress >= 75, progress >= 75 && progress < 90],
    ["Validating output", progress >= 100, progress >= 90 && progress < 100],
    ["Preparing files", status === "succeeded", false],
  ];

  return (
    <div className="page progress-page">
      <div className="progress-hero">
        <div
          className={`generation-orb ${
            status === "succeeded"
              ? "is-success"
              : status === "failed" || status === "cancelled"
                ? "is-error"
                : ""
          }`}
          aria-label={`Generation status: ${statusLabel}`}
        >
          <span>{status === "succeeded" ? "✓" : status === "failed" || status === "cancelled" ? "!" : ""}</span>
          <i />
        </div>
        <Badge tone={status === "succeeded" ? "success" : status === "failed" ? "danger" : status === "cancelled" ? "warning" : "accent"}>JOB #{job?.job_id?.slice(0, 8).toUpperCase() ?? "PENDING"} · {statusLabel}</Badge>
        <div className="page-title">{status === "failed" ? "Generation Failed" : status === "cancelled" ? "Generation Cancelled" : status === "succeeded" ? "Music Generation Complete" : "Generating Your Music"}</div>
        <div className="page-subtitle">{status === "failed" ? (job?.error?.message ?? "The generation job failed.") : status === "cancelled" ? "The generation job was cancelled." : status === "succeeded" ? "Module 2 specification → Module 3 stem generation completed successfully." : "Your request is being processed asynchronously by the music job worker."}</div>
      </div>
      <Card className="progress-card">
        <div className="progress-summary">
          <div><span>OVERALL PROGRESS</span><strong>{progress}%</strong></div>
          <div><span>STATUS</span><strong>{status}</strong></div>
          <div><span>DURATION</span><strong>{job?.duration_s ?? "—"} sec</strong></div>
        </div>
        <div className="large-progress"><span style={{ width: `${progress}%` }} /></div>
        <div className="pipeline">
          {steps.map(([label, done, active]) => (
            <div className={`pipeline-step ${done ? "is-done" : ""} ${active ? "is-active" : ""}`} key={label as string}>
              <div className="pipeline-marker">{done ? "✓" : active ? <span className="spinner" /> : "·"}</div>
              <div><strong>{label as string}</strong><small>{done ? "Completed" : active ? "In progress" : "Waiting"}</small></div>
              {active && <Badge tone="accent">RUNNING</Badge>}
            </div>
          ))}
        </div>
        {job?.prompt && <div className="prompt-summary"><span>REQUEST</span><p>{job.prompt}</p></div>}
        {job?.error && <div className="credibility-note"><span>!</span><div><strong>{job.error.code}</strong><small>{job.error.message}</small></div></div>}
        {job?.status === "succeeded" && job.job_id && (
          <Card className="stems-panel">
            <SectionHeader title="Generated Audio" detail="Play or download the real WAV files returned by Module 3" action={<Badge tone="success">READY</Badge>} />
            <div style={{ display: "grid", gap: 16 }}>
              {job.result && typeof job.result === "object" && "ducked_mix" in job.result ? (
                <div><strong>DUCKED MIX</strong><AudioPlayer src={duckedAudioUrl(job.job_id)} label="ducked mix" downloadName="mix_ducked.wav" /></div>
              ) : null}
              <div><strong>FINAL MIX</strong><AudioPlayer src={audioUrl(job.job_id, "mix")} label="final mix" downloadName="mix.wav" /></div>
              {generatedStems.map((stem) => {
                const stemId = stem.stem_id ?? stem.name;
                return <div key={stemId}><strong>{stem.name.toUpperCase()}</strong><AudioPlayer src={audioUrl(job.job_id, stemId)} label={stem.name} downloadName={`${stemId}.wav`} /></div>;
              })}
            </div>
          </Card>
        )}
        {job?.status === "succeeded" && typeof job.result?.message === "string" && (
          <div className="credibility-note"><span>✓</span><div><strong>Job completed successfully</strong><small>{String(job.result.message)}</small></div></div>
        )}
        {cancelError && <div className="credibility-note"><span>!</span><div><strong>Cancellation failed</strong><small>{cancelError}</small></div></div>}
        <div className="progress-actions">
          {status === "failed" && <Button variant="primary" onClick={onComplete}>Back to Create</Button>}
          {status !== "succeeded" && status !== "failed" && status !== "cancelled" && <Button variant="danger" disabled={cancelling} onClick={handleCancel}>{cancelling ? "Cancelling…" : "Cancel Job"}</Button>}
          {status === "succeeded" && <Button variant="primary" onClick={onComplete}>Open Studio</Button>}
          {status === "cancelled" && <Button variant="secondary" onClick={onComplete}>Back to Create</Button>}
        </div>
      </Card>
      {status !== "succeeded" && status !== "failed" && status !== "cancelled" && <div className="background-note"><span>✓</span><div><strong>You can continue working</strong><small>The job runs asynchronously. This page polls status every 1 second and stops at a terminal state.</small></div></div>}
    </div>
  );
}

function StemTrack({ name, color, onRegenerate }: { name: string; color: "violet" | "blue" | "teal" | "amber"; onRegenerate: (stem: string) => void }) {
  const [muted, setMuted] = useState(false);
  const [solo, setSolo] = useState(false);
  return (
    <div className={`stem-track ${muted ? "is-muted" : ""} ${solo ? "is-solo" : ""}`}>
      <div className="stem-identity"><span className={`track-color track-color--${color}`} /><div><strong>{name}</strong><small>INDEPENDENT STEM</small></div></div>
      <div className="track-controls"><IconButton label={`${muted ? "Unmute" : "Mute"} ${name}`} active={muted} onClick={() => setMuted(!muted)}>M</IconButton><IconButton label={`${solo ? "Unsolo" : "Solo"} ${name}`} active={solo} onClick={() => setSolo(!solo)}>S</IconButton></div>
      <div className="stem-wave"><Waveform compact color={color} muted={muted} /><span className="stem-playhead" /></div>
      <div className="mixer-control"><small>VOL</small><input type="range" min="0" max="100" defaultValue="72" /><strong>−3.0 dB</strong></div>
      <div className="pan-control"><small>PAN</small><span>L</span><input type="range" min="-100" max="100" defaultValue="0" /><span>R</span></div>
      <div><Button className="regen-button" onClick={() => onRegenerate(name)}>↻ Regenerate</Button></div>
    </div>
  );
}

function RegenerateModal({
  stem,
  onClose,
  onRegenerate,
  loading,
}: {
  stem: string;
  onClose: () => void;
  onRegenerate: (
    instruction: string,
    preserve: {
      tempoAndKey: boolean;
      timelineBoundaries: boolean;
      overallMood: boolean;
      masterDuration: boolean;
    }
  ) => void;
  loading: boolean;
}) {
  const [description, setDescription] = useState(
    `Regenerate the ${stem.toLowerCase()} stem while preserving the current musical context.`
  );

  const [preserve, setPreserve] = useState({
    tempoAndKey: true,
    timelineBoundaries: true,
    overallMood: true,
    masterDuration: true,
  });

  const submitRegeneration = () => {
    const instruction = description.trim();

    if (!instruction) {
      return;
    }

    onRegenerate(instruction, preserve);
  };

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div
        className="modal"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div>
            <span>STEM REGENERATION</span>
            <strong>Regenerate {stem}</strong>
          </div>

          <IconButton label="Close" onClick={onClose}>
            ×
          </IconButton>
        </div>

        <div className="current-description">
          <span>CURRENT STEM</span>
          <strong>{stem} · Independent audio stem</strong>
        </div>

        <label className="modal-field">
          <span>REGENERATION INSTRUCTION</span>

          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder={`Describe how the ${stem.toLowerCase()} stem should change...`}
            maxLength={2000}
          />

          <small style={{ display: "block", marginTop: 6, opacity: 0.65 }}>
            {description.length} / 2,000
          </small>
        </label>

        <div className="preserve-list">
          <span>PRESERVE</span>

          <label>
            <input
              type="checkbox"
              checked={preserve.tempoAndKey}
              onChange={(event) =>
                setPreserve((current) => ({
                  ...current,
                  tempoAndKey: event.target.checked,
                }))
              }
              disabled={loading}
            />
            <span>{preserve.tempoAndKey ? "✓" : ""}</span>
            Tempo and key
          </label>

          <label>
            <input
              type="checkbox"
              checked={preserve.timelineBoundaries}
              onChange={(event) =>
                setPreserve((current) => ({
                  ...current,
                  timelineBoundaries: event.target.checked,
                }))
              }
              disabled={loading}
            />
            <span>{preserve.timelineBoundaries ? "✓" : ""}</span>
            Timeline boundaries
          </label>

          <label>
            <input
              type="checkbox"
              checked={preserve.overallMood}
              onChange={(event) =>
                setPreserve((current) => ({
                  ...current,
                  overallMood: event.target.checked,
                }))
              }
              disabled={loading}
            />
            <span>{preserve.overallMood ? "✓" : ""}</span>
            Overall mood
          </label>

          <label>
            <input
              type="checkbox"
              checked={preserve.masterDuration}
              onChange={(event) =>
                setPreserve((current) => ({
                  ...current,
                  masterDuration: event.target.checked,
                }))
              }
              disabled={loading}
            />
            <span>{preserve.masterDuration ? "✓" : ""}</span>
            Master duration
          </label>
        </div>

        <div className="modal-actions">
          <Button onClick={onClose} disabled={loading}>
            Cancel
          </Button>

          <Button
            variant="primary"
            disabled={loading || !description.trim()}
            onClick={submitRegeneration}
          >
            {loading ? "Regenerating…" : "Regenerate Stem"}
          </Button>
        </div>
      </div>
    </div>
  );
}

function asNumber(value: unknown, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function asString(value: unknown, fallback = "—") {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function toAbsoluteMediaUrl(value: string): string {
  if (!value) return "";
  if (/^https?:\/\//i.test(value)) return value;
  if (value.startsWith("/")) return `${API_BASE_URL}${value}`;
  return value;
}

const qcTone = (status: QcStatus) => status === "PASS" ? "success" as const : status === "FAIL" ? "danger" as const : "neutral" as const;

export function Studio({ job, onJobRefresh }: { job: MusicJob | null; onJobRefresh?: (job: MusicJob) => void }) {
  const [stemModal, setStemModal] = useState<string | null>(null);
  const [manifest, setManifest] = useState(false);
  const [qcReport, setQcReport] = useState(false);
  const [stemUrls, setStemUrls] = useState<Record<string, string>>({});
  const [mixUrl, setMixUrl] = useState("");
  const [regenerating, setRegenerating] = useState<Record<string, boolean>>({});
  const [regenError, setRegenError] = useState("");
  // Cache-bust only the stem that was regenerated (and the rebuilt mix).
  // This prevents the browser from continuing to play a cached WAV file.
  const [audioVersions, setAudioVersions] = useState<Record<string, string>>({});
  const masterAudioRef = useRef<HTMLAudioElement | null>(null);
  const [masterPlaying, setMasterPlaying] = useState(false);
  const [masterTime, setMasterTime] = useState(0);
  const [masterDuration, setMasterDuration] = useState(0);
  const [masterAudioError, setMasterAudioError] = useState("");
  const [validation, setValidation] = useState<Awaited<ReturnType<typeof getMusicValidation>> | null>(null);
  const [validationError, setValidationError] = useState("");
  const [loopValidation, setLoopValidation] = useState<Awaited<ReturnType<typeof getLoopValidation>> | null>(null);
  const [loopValidationError, setLoopValidationError] = useState("");
  const [validationRevision, setValidationRevision] = useState(0);
  const [validationLoading, setValidationLoading] = useState(false);
  const [evidenceOwner, setEvidenceOwner] = useState<MusicJob | null>(null);
  const latestJob = useRef(job);
  latestJob.current = job;
  const [packageDownloading, setPackageDownloading] = useState(false);
  const [packageError, setPackageError] = useState("");
  const [soloedStem, setSoloedStem] = useState<string | null>(null);
  const [mutedStems, setMutedStems] = useState<Record<string, boolean>>({});
  const [stemProgress, setStemProgress] = useState<Record<string, number>>({});
  const [resetMixToken, setResetMixToken] = useState(0);
  const stemAudioRefs = useRef<Record<string, HTMLAudioElement | null>>({});

  useEffect(() => {
    setStemUrls({});
    setMixUrl("");
    setAudioVersions({});
    setRegenError("");
    setValidation(null);
    setValidationError("");
    setLoopValidation(null);
    setLoopValidationError("");
    setPackageDownloading(false);
    setPackageError("");
    setSoloedStem(null);
    setMutedStems({});
    setStemProgress({});
  }, [job?.job_id, job?.status]);

  useEffect(() => {
    let cancelled = false;
    setEvidenceOwner(job);
    setValidation(null);
    setLoopValidation(null);
    setValidationError("");
    setLoopValidationError("");
    setQcReport(false);
    if (!job?.job_id || job.status !== "succeeded") {
      setValidationLoading(false);
      return;
    }
    const jobId = job.job_id;
    setValidationLoading(true);
    // A failed stem listing must not hide a readable backend QC failure report.
    void getMusicStems(jobId).then(data => {
      if (cancelled) return;
      setStemUrls(Object.fromEntries(data.stems.map(stem => [stem.stem_id ?? stem.name, toAbsoluteMediaUrl(stem.url)])));
      setMixUrl(toAbsoluteMediaUrl(data.mix_url));
    }).catch(error => {
      if (!cancelled) setRegenError(error instanceof Error ? error.message : "Unable to load generated stems.");
    });
    void (async () => {
      try {
        const data = await getMusicValidation(jobId);
        if (!data || data.job_id !== jobId) throw new Error("Validation response does not match the active job.");
        if (cancelled) return;
        setValidation(data);
        // Current Module 8 checks are authoritative. Only older responses
        // without a loop check need the separate fresh loop endpoint.
        if (!("looping" in validationEvidence(data).checks)) {
          try {
            const loop = await getLoopValidation(jobId);
            if (!cancelled && loop?.job_id === jobId) setLoopValidation(loop);
          } catch (error) {
            if (!cancelled && !(error instanceof ApiError && error.status === 409)) {
              setLoopValidationError(error instanceof Error ? error.message : "Loop evidence unavailable.");
            }
          }
        }
        if (!cancelled) setAudioVersions(current => ({ ...current, mix: String(Date.now()) }));
      } catch (error) {
        if (!cancelled) {
          setValidation(null);
          setLoopValidation(null);
          setValidationError(error instanceof Error ? error.message : "Unable to load measured validation.");
        }
      } finally {
        if (!cancelled) setValidationLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [job, validationRevision]);

  const operationRunning = Object.values(regenerating).some(Boolean);
  const currentEvidence = evidenceOwner === job && !validationLoading && !operationRunning;
  const freshValidation = currentEvidence ? validation : null;
  const qc = validationEvidence(freshValidation);
  const freshLoop = freshValidation && "looping" in qc.checks ? qc.check("looping")
    : currentEvidence && !validationError ? record(loopValidation?.loop) : {};
  const duckCheck = qc.check("ducking");
  const moduleStatus = freshValidation ? qc.status : "UNAVAILABLE";
  const qcState = validationLoading || operationRunning || evidenceOwner !== job ? "REFRESHING" : !job ? "NO JOB" : validationError ? "UNAVAILABLE" : moduleStatus;
  const formatCheck = qc.check("format");
  const formatDescription = `${text(formatCheck.container ?? qc.av.format)} · ${text(formatCheck.subtype ?? qc.av.subtype)} · ${measured(formatCheck.bit_depth, "bit")} · ${measured(formatCheck.channels ?? qc.av.channels, "channels")}`;

  const refreshValidation = () => {
    setValidation(null);
    setLoopValidation(null);
    setValidationRevision(value => value + 1);
  };

  useEffect(() => {
    const audio = masterAudioRef.current;
    if (!audio) return;

    audio.pause();
    audio.currentTime = 0;
    setMasterPlaying(false);
    setMasterTime(0);
    setMasterDuration(0);
    setMasterAudioError("");

    const src = mixUrl || (job?.job_id ? audioUrl(job.job_id, "mix") : "");
    if (src) {
      audio.load();
    }
  }, [job?.job_id, mixUrl]);

  const toggleMasterPlayback = async () => {
    const audio = masterAudioRef.current;
    if (!audio) return;

    setMasterAudioError("");

    try {
      if (audio.paused) {
        await audio.play();
      } else {
        audio.pause();
      }
    } catch {
      setMasterPlaying(false);
      setMasterAudioError("Unable to play the final mix. Use Download Mix to verify the WAV file.");
    }
  };

  const seekMaster = (event: React.MouseEvent<HTMLDivElement>) => {
    const audio = masterAudioRef.current;
    if (!audio || !masterDuration) return;

    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    audio.currentTime = ratio * masterDuration;
    setMasterTime(audio.currentTime);
  };

 const handleRegenerate = async (
  stem: string,
  instruction: string,
  preserve: {
    tempoAndKey: boolean;
    timelineBoundaries: boolean;
    overallMood: boolean;
    masterDuration: boolean;
  }
) => {
  if (!job?.job_id || regenerating[stem]) return;

  const normalizedStem = stem;

  const validStems = new Set(
    job?.result && typeof job.result === "object" && "stems" in job.result
      ? (job.result.stems as Array<{ stem_id?: string; name: string }>).map((item) => item.stem_id ?? item.name)
      : [],
  );

  if (!validStems.has(normalizedStem)) {
    setRegenError(`Invalid stem: ${stem}`);
    return;
  }

  setRegenerating((current) => ({
    ...current,
    [normalizedStem]: true,
  }));

  setRegenError("");
  setValidation(null);
  setLoopValidation(null);
  setValidationLoading(true);

  try {
    const accepted = await regenerateStem(
      job.job_id,
      normalizedStem,
      {
        instruction,
        preserve,
      }
    );

    const completed = await waitForMusicJob(
      accepted.job_id,
      () => undefined,
      500
    );
    if (latestJob.current?.job_id !== job.job_id) return;

    if (completed.status !== "succeeded") {
      throw new Error(
        completed.error?.message ||
          `Unable to regenerate ${normalizedStem}.`
      );
    }

    // Reload the latest stem URLs from backend
    const data = await getMusicStems(job.job_id);

    setStemUrls((current) => ({
      ...current,
      ...Object.fromEntries(
        data.stems.map((item) => [
          item.stem_id ?? item.name,
          item.url,
        ])
      ),
    }));

    setMixUrl(data.mix_url);

    // Cache bust the regenerated audio
    const version = String(Date.now());

    setAudioVersions((current) => ({
      ...current,
      [normalizedStem]: version,
      mix: version,
    }));

    // Re-fetch the authoritative parent rather than merging child validation.
    if (onJobRefresh) {
      const parent = await getMusicJob(job.job_id);
      if (latestJob.current?.job_id === job.job_id) onJobRefresh(parent);
    }

    setStemModal(null);
  } catch (error) {
    setRegenError(
      error instanceof ApiError
        ? error.message
        : error instanceof Error
          ? error.message
          : `Unable to regenerate ${normalizedStem}.`
    );
  } finally {
    if (latestJob.current?.job_id === job.job_id) refreshValidation();
    setRegenerating((current) => ({
      ...current,
      [normalizedStem]: false,
    }));
  }
};

  const getStemSrc = (stemId: string) => {
    const base = stemUrls[stemId] || (job?.job_id ? audioUrl(job.job_id, stemId) : "");
    const version = audioVersions[stemId];
    if (!base || !version) return base;
    return `${base}${base.includes("?") ? "&" : "?"}v=${encodeURIComponent(version)}`;
  };

  const getMixSrc = () => {
    const base = mixUrl || (job?.job_id ? audioUrl(job.job_id, "mix") : "");
    const version = audioVersions.mix;
    if (!base || !version) return base;
    return `${base}${base.includes("?") ? "&" : "?"}v=${encodeURIComponent(version)}`;
  };

  const getDuckedMixSrc = () => {
    if (!job?.job_id || duckCheck.enabled !== true) return "";
    const explicitUrl = typeof job.result === "object" && job.result && "ducked_mix" in job.result && job.result.ducked_mix && typeof (job.result as { ducked_mix?: { url?: string } }).ducked_mix?.url === "string"
      ? toAbsoluteMediaUrl((job.result as { ducked_mix?: { url?: string } }).ducked_mix?.url ?? "")
      : "";
    const base = explicitUrl || duckedAudioUrl(job.job_id);
    return `${base}${base.includes("?") ? "&" : "?"}v=${encodeURIComponent(audioVersions.mix ?? job.updated_at)}`;
  };

  const getLoopedMixSrc = () => {
    if (!job?.job_id || freshLoop.enabled === false || Object.keys(freshLoop).length === 0) return "";
    const loopedMix = typeof job.result === "object" && job.result && "looped_mix" in job.result && job.result.looped_mix && typeof (job.result as { looped_mix?: { url?: string } }).looped_mix?.url === "string"
      ? (job.result as { looped_mix?: { url?: string } }).looped_mix?.url ?? ""
      : "";
    const base = toAbsoluteMediaUrl(loopedMix) || loopAudioUrl(job.job_id);
    return `${base}${base.includes("?") ? "&" : "?"}v=${encodeURIComponent(audioVersions.mix ?? job.updated_at)}`;
  };

  const handleDownloadPackage = async () => {
    if (!job?.job_id || packageDownloading) return;
    setPackageDownloading(true);
    setPackageError("");
    try {
      await downloadJobPackage(job.job_id);
    } catch (error) {
      setPackageError(error instanceof ApiError ? error.message : "Unable to download the package.");
    } finally {
      setPackageDownloading(false);
    }
  };

  const jobManifest = (typeof job?.result === "object" && job.result && "manifest" in job.result ? (job.result as { manifest?: Record<string, unknown> }).manifest : null) ?? null;
  const manifestSections = Array.isArray(jobManifest?.sections)
    ? (jobManifest.sections as Array<{ name?: string; start_s?: number; end_s?: number; duration_s?: number; start?: number; end?: number }>)
    : [];
  const manifestGenre = asString(jobManifest?.genre, "—");
  const manifestMood = Array.isArray(jobManifest?.mood)
    ? (jobManifest.mood as string[]).filter((value) => typeof value === "string" && value.trim())
    : [];
  const manifestInstruments = Array.isArray(jobManifest?.instruments)
    ? (jobManifest.instruments as string[]).filter((value) => typeof value === "string" && value.trim())
    : [];
  const manifestStemCount = asNumber(jobManifest?.stem_count ?? manifestInstruments.length, manifestInstruments.length);
  const manifestBpm = asNumber(jobManifest?.bpm ?? (typeof job?.result === "object" && job.result && "bpm" in job.result ? (job.result as { bpm?: number }).bpm : 0), 0);
  const manifestKey = asString(jobManifest?.key ?? (typeof job?.result === "object" && job.result && "key" in job.result ? (job.result as { key?: string }).key : ""), "—");
  const manifestDuration = asNumber(jobManifest?.duration_s ?? (manifestSections.length ? Math.max(...manifestSections.map((section) => Number(section.end_s ?? section.end ?? 0))) : job?.duration_s ?? 0), 0);
  const beatsPerBar = asNumber(jobManifest?.beats_per_bar ?? 4, 4);
  const beatDuration = asNumber(jobManifest?.beat_duration_s ?? (manifestBpm > 0 ? 60 / manifestBpm : 0), 0);
  const barDuration = asNumber(jobManifest?.bar_duration_s ?? (beatDuration > 0 ? beatDuration * beatsPerBar : 0), 0);
  const sectionMaxDuration = Math.max(manifestDuration, ...manifestSections.map((section) => Number(section.end_s ?? section.end ?? 0)), 1);
  const timeTicks = Array.from({ length: 5 }, (_, index) => (manifestDuration * index) / 4);

  const loopStatus = evidenceStatus(freshLoop);
  const loopStart = numeric(freshLoop.loop_start_s);
  const loopEnd = numeric(freshLoop.loop_end_s);
  const loopedMixSrc = getLoopedMixSrc();
  const hasLoopData = Object.keys(freshLoop).length > 0 && freshLoop.enabled !== false;

  return (
    <div className="studio-page">
      {hasLoopData && (
        <Card className="loop-card">
          <SectionHeader title="Seamless Looping" detail="Measured from the rendered loop boundary" action={<Badge tone={qcTone(loopStatus)}>{loopStatus}</Badge>} />
          <div className="detail-row"><span>Loop enabled</span><strong>{yesNo(freshLoop.enabled)}</strong></div>
          <div className="detail-row"><span>Loop validation</span><strong>{loopStatus}</strong></div>
          <div className="detail-row"><span>Loop start</span><strong>{measured(loopStart, "sec", 6)}</strong></div>
          <div className="detail-row"><span>Loop end</span><strong>{measured(loopEnd, "sec", 6)}</strong></div>
          <div className="detail-row"><span>Loop duration</span><strong>{measured(freshLoop.loop_duration_s, "sec", 2)}</strong></div>
          <div className="detail-row"><span>Measured boundary discontinuity</span><strong>{measured(freshLoop.boundary_discontinuity)}</strong></div>
          <div className="detail-row"><span>Bar alignment (start / end)</span><strong>{yesNo(freshLoop.start_aligned_to_bar)} / {yesNo(freshLoop.end_aligned_to_bar)}</strong></div>
          {typeof freshLoop.reason === "string" && <small>{freshLoop.reason}</small>}
          {loopedMixSrc && (
            <div style={{ marginTop: 16 }}>
              <strong>LOOPED OUTPUT</strong>
              <AudioPlayer
                src={loopedMixSrc}
                label="looped mix"
                downloadName="mix_looped.wav"
                loopRange={loopStatus === "PASS" && loopStart !== undefined && loopEnd !== undefined && loopEnd > loopStart ? { start: loopStart, end: loopEnd } : null}
              />
              <div style={{ marginTop: 8 }}>
                <a className="button" href={loopedMixSrc} download="mix_looped.wav" target="_blank" rel="noreferrer">⇩ Download Loop</a>
              </div>
            </div>
          )}
          {loopValidationError && <small style={{ display: "block", marginTop: 8, color: "#ff8b8b" }}>{loopValidationError}</small>}
        </Card>
      )}

      <div className="studio-header">
        <div>
          <div className="breadcrumb">PROJECTS / PRODUCT LAUNCH / <span>VERSION 4</span></div>
          <div className="studio-title-row"><div className="page-title">Generated Music Session</div><Badge tone={qcTone(moduleStatus)}>Module 8 {qcState}</Badge></div>
          <div className="metadata"><span>Requested {manifestDuration || (job?.duration_s ?? 0)} sec</span><i /> <span>{manifestGenre}</span><i /> <span>{manifestStemCount || manifestInstruments.length || 0} requested instruments</span><i /> <span>{manifestMood.join(", ") || "—"}</span></div>
        </div>
        <div className="header-buttons"><Button>↻ Regenerate</Button><Button variant="primary" onClick={() => job?.job_id && window.open(getMixSrc(), "_blank")}>⇩ Export</Button></div>
      </div>

      <Card className="master-player">
        {getDuckedMixSrc() && (
          <div style={{ marginBottom: 16 }}>
            <strong>DUCKED MIX</strong>
            <AudioPlayer src={getDuckedMixSrc()} label="ducked mix" downloadName="mix_ducked.wav" />
          </div>
        )}
        <div className="manifest-summary" style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", marginBottom: 16 }}>
          <div><span style={{ fontSize: 10, textTransform: "uppercase", opacity: 0.7 }}>GENRE</span><strong style={{ display: "block", marginTop: 4 }}>{manifestGenre}</strong></div>
          <div><span style={{ fontSize: 10, textTransform: "uppercase", opacity: 0.7 }}>MOOD</span><strong style={{ display: "block", marginTop: 4 }}>{manifestMood.join(", ") || "—"}</strong></div>
          <div><span style={{ fontSize: 10, textTransform: "uppercase", opacity: 0.7 }}>INSTRUMENTS</span><strong style={{ display: "block", marginTop: 4 }}>{manifestStemCount || manifestInstruments.length || "—"}</strong></div>
          <div><span style={{ fontSize: 10, textTransform: "uppercase", opacity: 0.7 }}>BPM</span><strong style={{ display: "block", marginTop: 4 }}>{manifestBpm || "—"}</strong></div>
          <div><span style={{ fontSize: 10, textTransform: "uppercase", opacity: 0.7 }}>KEY</span><strong style={{ display: "block", marginTop: 4 }}>{manifestKey}</strong></div>
          <div><span style={{ fontSize: 10, textTransform: "uppercase", opacity: 0.7 }}>BEATS/BAR</span><strong style={{ display: "block", marginTop: 4 }}>{beatsPerBar}</strong></div>
          <div><span style={{ fontSize: 10, textTransform: "uppercase", opacity: 0.7 }}>BEAT</span><strong style={{ display: "block", marginTop: 4 }}>{beatDuration > 0 ? `${beatDuration.toFixed(3)}s` : "—"}</strong></div>
          <div><span style={{ fontSize: 10, textTransform: "uppercase", opacity: 0.7 }}>BAR</span><strong style={{ display: "block", marginTop: 4 }}>{barDuration > 0 ? `${barDuration.toFixed(3)}s` : "—"}</strong></div>
        </div>
        {manifestSections.length > 0 && (
          <div className="section-strip">
            {manifestSections.map((section) => {
              const start = Number(section.start_s ?? section.start ?? 0);
              const end = Number(section.end_s ?? section.end ?? 0);
              const width = Math.max(8, ((end - start) / Math.max(sectionMaxDuration, 1)) * 100);
              return (
                <div key={`${section.name ?? "section"}-${start}-${end}`} style={{ width: `${width}%` }}>
                  <span>{section.name ?? "SECTION"}</span>
                  <small>{formatAudioTime(start)}–{formatAudioTime(end)}</small>
                </div>
              );
            })}
          </div>
        )}
        <div
          className="master-waveform"
          onClick={seekMaster}
          role="slider"
          aria-label="Final mix playback position"
          aria-valuemin={0}
          aria-valuemax={masterDuration || manifestDuration || job?.duration_s || 0}
          aria-valuenow={masterTime}
          tabIndex={0}
          title="Click the waveform to seek"
        >
          <div className="time-ruler">
            {timeTicks.map((tick, index) => (
              <span key={`${tick}-${index}`}>{formatAudioTime(tick)}</span>
            ))}
          </div>
          <Waveform />
          <span
            className="master-playhead"
            style={{
              left: `${masterDuration > 0 ? Math.min(100, Math.max(0, (masterTime / masterDuration) * 100)) : 0}%`,
            }}
          >
            <i>{formatAudioTime(masterTime)}</i>
          </span>
        </div>

        <div className="transport">
          <div className="transport-left">
            <IconButton
              label="Previous"
              onClick={() => {
                const audio = masterAudioRef.current;
                if (audio) {
                  audio.currentTime = 0;
                  setMasterTime(0);
                }
              }}
            >
              |◀
            </IconButton>

            <button
              className="play-button"
              type="button"
              onClick={() => void toggleMasterPlayback()}
              disabled={!job?.job_id}
              aria-label={masterPlaying ? "Pause final mix" : "Play final mix"}
              title={masterPlaying ? "Pause final mix" : "Play final mix"}
            >
              {masterPlaying ? "Ⅱ" : "▶"}
            </button>

            <IconButton
              label="Next"
              onClick={() => {
                const audio = masterAudioRef.current;
                if (audio && masterDuration > 0) {
                  audio.currentTime = Math.min(masterDuration, audio.currentTime + 10);
                  setMasterTime(audio.currentTime);
                }
              }}
            >
              ▶|
            </IconButton>
          </div>

          <div className="transport-time">
            <strong>FINAL MIX</strong>
            <span>
              {formatAudioTime(masterTime)} / {formatAudioTime(masterDuration || job?.duration_s || 0)}
            </span>
          </div>

          <div className="transport-right" style={{ minWidth: 0, flex: 1 }}>
            {job?.job_id && (
              <audio
                ref={masterAudioRef}
                controls
                preload="auto"
                src={getMixSrc()}
                style={{ width: "100%", display: "block" }}
                onLoadedMetadata={(event) => {
                  const value = event.currentTarget.duration;
                  if (Number.isFinite(value)) setMasterDuration(value);
                }}
                onDurationChange={(event) => {
                  const value = event.currentTarget.duration;
                  if (Number.isFinite(value)) setMasterDuration(value);
                }}
                onTimeUpdate={(event) => setMasterTime(event.currentTarget.currentTime)}
                onPlay={(event) => {
                  console.log("[Studio] master play", { src: getMixSrc(), currentSrc: event.currentTarget.currentSrc });
                  setMasterPlaying(true);
                  setMasterAudioError("");
                }}
                onPause={() => setMasterPlaying(false)}
                onEnded={() => {
                  setMasterPlaying(false);
                  setMasterTime(0);
                }}
                onError={(event) => {
                  const mediaError = event.currentTarget.error;
                  console.log("[Studio] master media error", { src: getMixSrc(), code: mediaError?.code, message: mediaError?.message, currentSrc: event.currentTarget.currentSrc, networkState: event.currentTarget.networkState, readyState: event.currentTarget.readyState });
                  setMasterPlaying(false);
                  setMasterAudioError("The final mix WAV could not be loaded by the browser.");
                }}
              >
                <source src={getMixSrc()} type="audio/wav" />
              </audio>
            )}

            {job?.job_id && (
              <a className="button" href={getMixSrc()} download="mix.wav" target="_blank" rel="noreferrer">
                ⇩ Download Mix
              </a>
            )}

            {masterAudioError && (
              <small style={{ display: "block", marginTop: 6, color: "#ff8b8b" }}>
                {masterAudioError}
              </small>
            )}
          </div>
        </div>
      </Card>

      {regenError && <div className="credibility-note"><span>!</span><div><strong>Stem regeneration</strong><small>{regenError}</small></div></div>}

      <div className="studio-layout">
        <div className="studio-main">
          <Card className="stems-panel">
            <SectionHeader title="Stem Mixer" detail="Rendered instrument tracks · measured alignment is shown in QC" action={<div className="mixer-actions"><Button variant="ghost" onClick={() => { setSoloedStem(null); setMutedStems({}); setResetMixToken((value) => value + 1); }}>Reset mix</Button><Button variant="ghost">Collapse all</Button></div>} />
            <div className="mixer-labels"><span>TRACK</span><span>WAVEFORM</span><span>PLAYER</span><span>DOWNLOAD</span><span>ACTIONS</span></div>
            {(job?.result && "stems" in job.result ? (job.result.stems as Array<{ stem_id?: string; name: string }>) : []).map((stem, index) => {
              const stemId = stem.stem_id ?? stem.name;
              const color = (["violet", "blue", "teal", "amber"] as const)[index % 4];
              const muted = Boolean(mutedStems[stemId]) || (soloedStem !== null && soloedStem !== stemId);
              const progress = stemProgress[stemId] ?? 0;
              return (
                <div className={`stem-track ${muted ? "is-muted" : ""} ${soloedStem === stemId ? "is-solo" : ""}`} key={stemId}>
                  <div className="stem-identity">
                    <span className={`track-color track-color--${color}`} />
                    <div><strong>{stem.name.toUpperCase()}</strong><small>INDEPENDENT STEM · WAV</small></div>
                  </div>
                  <div className="stem-wave" role="img" aria-label={`${stem.name} waveform`}>
                    <Waveform compact color={color} muted={muted} />
                    <span className="stem-playhead" style={{ left: `${progress}%` }} />
                  </div>
                  {job?.job_id && <AudioPlayer
                    src={getStemSrc(stemId)}
                    label={stem.name}
                    downloadName={`${stemId}.wav`}
                    muted={muted}
                    solo={soloedStem === stemId}
                    resetToken={resetMixToken}
                    audioRef={(element) => { stemAudioRefs.current[stemId] = element; }}
                    onSolo={() => setSoloedStem((current) => current === stemId ? null : stemId)}
                    onMute={() => setMutedStems((current) => ({ ...current, [stemId]: !current[stemId] }))}
                    onProgress={(current, duration) => setStemProgress((state) => ({ ...state, [stemId]: duration > 0 ? (current / duration) * 100 : 0 }))}
                  />}
                  <Button className="regen-button" disabled={Boolean(regenerating[stemId])} onClick={() => setStemModal(stemId)}>{regenerating[stemId] ? "Regenerating…" : "↻ Regenerate"}</Button>
                </div>
              );
            })}
            <div className="master-bus"><div><span className="track-color" /><strong>MASTER BUS</strong></div><span>{measured(qc.check("loudness").measured_lufs ?? qc.av.measured_lufs, "LUFS", 1)}</span><div className="meter" aria-hidden="true"><i /><i /><i /><i /><i /><i /><i /></div><strong>{measured(qc.check("true_peak").measured_dbtp ?? qc.av.true_peak_dbTP, "dBTP", 3)}</strong></div>
          </Card>

          <Card className="voiceover-panel">
            <SectionHeader title="Voiceover & Ducking" detail="Measured from mix, ducked mix and voiceover audio" action={<Badge tone={qcTone(evidenceStatus(duckCheck))}>{evidenceStatus(duckCheck)}</Badge>} />
            <div className="detail-row"><span>Ducking enabled</span><strong>{yesNo(duckCheck.enabled)}</strong></div>
            {duckCheck.enabled === true && <>
              <div className="detail-row"><span>Measured attenuation</span><strong>{measured(duckCheck.ducking_db, "dB")}</strong></div>
              <div className="detail-row"><span>Requirement target range</span><strong>8–15 dB</strong></div>
              <div className="detail-row"><span>Recovery after speech</span><strong>{yesNo(duckCheck.recovers_after_speech)}</strong></div>
              <div className="detail-row"><span>Transitions smooth</span><strong>{yesNo(duckCheck.transitions_smooth)}</strong></div>
              <div className="detail-row"><span>Music RMS before / during / after</span><strong>{measured(duckCheck.music_rms_before)} / {measured(duckCheck.music_rms_during_speech)} / {measured(duckCheck.music_rms_after_speech)}</strong></div>
              <div className="detail-row"><span>Detected speech regions</span><strong>{Array.isArray(duckCheck.speech_regions) ? duckCheck.speech_regions.length : "Unavailable"}</strong></div>
              {Array.isArray(duckCheck.speech_regions) && duckCheck.speech_regions.map((region, index) => <div className="detail-row" key={index}><span>Speech region {index + 1}</span><strong>{measured(record(region).start_s, "sec")}–{measured(record(region).end_s, "sec")}</strong></div>)}
            </>}
            {typeof duckCheck.reason === "string" && <small>{duckCheck.reason}</small>}
          </Card>
        </div>

        <aside className="inspector">
          <Card className="quality-card">
            <SectionHeader title="Audio Quality" detail="Module 8 · rendered WAV measurements" action={<Badge tone={qcTone(moduleStatus)}>{qcState}</Badge>} />
            {freshValidation && <div className="verified-note"><span>{moduleStatus === "PASS" ? "✓" : "i"}</span><div><strong>Backend measured evidence</strong><small>Targets describe requirements; measured values describe rendered files.</small></div></div>}
            {freshValidation ? qualityRows(Object.keys(freshLoop).length ? { ...freshValidation, checks: { ...qc.checks, looping: freshLoop } } : freshValidation).map(row => (
              <div className="quality-row" key={row.name} data-qc-check={row.name}><span>{row.name}</span><div><strong>{row.value}</strong><small>{row.note}</small>{row.reason && <small>{row.reason}</small>}</div><Badge tone={qcTone(row.status)}>{row.status}</Badge></div>
            )) : <div role="status" style={{ padding: "14px 0", color: "var(--muted)" }}>{validationError || (!job ? "Generate or load a job to view measured QC." : job.status !== "succeeded" ? "Measured QC is unavailable until generation succeeds. Inspect job errors for validation failures." : "Loading fresh backend validation…")}</div>}
            {freshValidation && Object.entries(record(qc.check("stems").details)).map(([name, value]) => {
              const stem = record(value);
              return <div className="quality-row" key={name} data-qc-stem={name}><span>{name}</span><div><strong>{stem.exists === false ? "Missing file" : stem.readable === false ? "Unreadable file" : `Duration ${measured(stem.duration_s, "sec")}`}</strong><small>Exists {yesNo(stem.exists)} · Readable {yesNo(stem.readable)} · {measured(stem.sample_rate_hz, "Hz")}</small>{typeof stem.reason === "string" && <small>{stem.reason}</small>}</div><Badge tone={qcTone(evidenceStatus(stem))}>{evidenceStatus(stem)}</Badge></div>;
            })}
            <Button variant="ghost" className="full-button" disabled={!freshValidation} onClick={() => setQcReport(true)}>View validation report →</Button>
            <Button variant="ghost" className="full-button" disabled={!job || job.status !== "succeeded" || validationLoading || Object.values(regenerating).some(Boolean)} onClick={refreshValidation}>Refresh measured QC</Button>
          </Card>
          <Card className="details-card">
            <SectionHeader title="Technical Details" />
            {(() => {
              return [
                ["Job ID", job?.job_id ?? "—"],
                ["Model", "IncuBrix Music Engine"],
                ["Provider", "Local deterministic engine"],
                ["Seed", measured(record(job?.result).seed)],
                ["Measured format", formatDescription],
                ["Measured channels", measured(formatCheck.channels ?? qc.av.channels)],
                ["Created", job?.created_at ? new Date(job.created_at).toLocaleString() : "—"],
              ].map(([key, value]) => <div className="detail-row" key={key}><span>{key}</span><strong>{value}</strong></div>);
            })()}
            <Button className="full-button" onClick={() => setManifest(true)}>Manifest JSON</Button>
          </Card>
          <Card className="export-card">
            <SectionHeader title="Export" />
            <label><input type="checkbox" defaultChecked /><span>✓</span><div><strong>Master Mix</strong><small>{formatDescription}</small></div></label>
            <label><input type="checkbox" defaultChecked /><span>✓</span><div><strong>Requested Instruments</strong><small>{manifestStemCount || manifestInstruments.length || 0} selected in the specification</small></div></label>
            <label><input type="checkbox" defaultChecked /><span>✓</span><div><strong>Generation Manifest</strong><small>JSON metadata</small></div></label>
            <Button variant="primary" className="full-button" disabled={!job?.job_id || packageDownloading} onClick={() => void handleDownloadPackage()}>{packageDownloading ? "Preparing Package..." : "⇩ Download Package"}</Button>
            {packageError && <small style={{ display: "block", marginTop: 8, color: "#ff8b8b" }}>{packageError}</small>}
          </Card>
        </aside>
      </div>
      {qcReport && freshValidation && <div className="modal-backdrop" onMouseDown={() => setQcReport(false)}><div className="modal manifest-modal" onMouseDown={event => event.stopPropagation()} role="dialog" aria-label="Module 8 measured validation report"><div className="modal-header"><strong>Module 8 measured validation report</strong><IconButton label="Close QC report" onClick={() => setQcReport(false)}>×</IconButton></div><pre>{JSON.stringify(freshValidation, null, 2)}</pre><div className="modal-actions"><Button onClick={() => setQcReport(false)}>Close report</Button></div></div></div>}
      {stemModal && (
  <RegenerateModal
    stem={stemModal}
    loading={Boolean(regenerating[stemModal])}
    onClose={() => {
      if (!regenerating[stemModal]) {
        setStemModal(null);
      }
    }}
    onRegenerate={(instruction, preserve) =>
      void handleRegenerate(
        stemModal,
        instruction,
        preserve
      )
    }
  />
)}
      {manifest && (
        <div className="modal-backdrop" onMouseDown={() => setManifest(false)}>
          <div className="modal manifest-modal" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-header"><div><span>TECHNICAL DETAILS</span><strong>Generation Manifest</strong></div><IconButton label="Close" onClick={() => setManifest(false)}>×</IconButton></div>
            <pre>{JSON.stringify({
              job_id: job?.job_id,
              duration_s: job?.duration_s,
              generation_result: job?.result ?? null,
              validation: freshValidation,
            }, null, 2)}</pre>
            <div className="modal-actions"><Button onClick={() => setManifest(false)}>Close</Button></div>
          </div>
        </div>
      )}
    </div>
  );
}

function Jobs({ openStudio }: { openStudio: () => void }) {
  const [filter, setFilter] = useState("All");
  const jobs = [
    ["#A82F91", "Cinematic, inspiring background", "Today, 10:42", "45s", "Succeeded", 4],
    ["#7C31E8", "Energetic electronic", "Today, 10:21", "30s", "Processing", 4],
    ["#61C204", "Calm documentary", "Today, 09:58", "60s", "Succeeded", 4],
  ] as const;
  return (
    <div className="page">
      <div className="page-heading"><div><div className="page-title">Generation Jobs</div><div className="page-subtitle">Monitor generation activity, inspect results, and retry failures.</div></div><Button variant="primary">+ New Generation</Button></div>
      <div className="dashboard-stats">
        {[["24", "TOTAL JOBS", "+8 this week"], ["2", "PROCESSING", "Average 2m 14s"], ["19", "SUCCEEDED", "95% success rate"], ["3", "FAILED", "Requires attention"]].map(([value, label, detail]) => <Card key={label}><span>{label}</span><strong>{value}</strong><small>{detail}</small></Card>)}
      </div>
      <Card className="table-card">
        <div className="table-toolbar"><div className="tabs">{["All", "Queued", "Processing", "Succeeded", "Failed"].map((item) => <button className={filter === item ? "is-active" : ""} onClick={() => setFilter(item)} key={item}>{item}</button>)}</div><div><button className="filter-button">≡ Filter</button><button className="filter-button">↕ Sort</button></div></div>
        <div className="data-table">
          <div className="table-row table-head"><span>JOB ID</span><span>PROMPT</span><span>CREATED</span><span>DURATION</span><span>STATUS</span><span>STEMS</span><span /></div>
          {jobs.filter((job) => filter === "All" || job[4] === filter).map((job) => (
            <button className="table-row" key={job[0]} onClick={job[4] === "Succeeded" ? openStudio : undefined}>
              <strong>{job[0]}</strong><span>{job[1]}</span><span>{job[2]}</span><span>{job[3]}</span><span><Badge tone={job[4] === "Succeeded" ? "success" : job[4] === "Processing" ? "accent" : job[4] === "Failed" ? "danger" : "neutral"}>{job[4]}</Badge></span><span>{job[5]} tracks</span><span className="row-action">•••</span>
            </button>
          ))}
        </div>
      </Card>
    </div>
  );
}

function Projects({ openStudio }: { openStudio: () => void }) {
  return (
    <div className="page">
      <div className="page-heading"><div><div className="page-title">Projects</div><div className="page-subtitle">Organize music created for your videos and campaigns.</div></div><Button variant="primary">+ New Project</Button></div>
      <div className="library-toolbar"><div className="search-field">⌕ <input placeholder="Search projects…" /></div><div><Button>Last modified ⌄</Button><IconButton label="Grid view" active>▦</IconButton><IconButton label="List view">☷</IconButton></div></div>
      <div className="project-grid">
        {[
          ["Product Launch Film", "Corporate Technology Theme", "45 sec", "4 stems", "Today, 10:48", "violet"],
          ["Q3 Brand Campaign", "Forward Motion", "60 sec", "5 stems", "Yesterday", "blue"],
          ["Customer Stories", "Human Connection", "90 sec", "4 stems", "Jun 10", "teal"],
          ["Platform Overview", "Digital Horizon", "30 sec", "3 stems", "Jun 8", "amber"],
          ["Documentary Series", "Under the Surface", "180 sec", "6 stems", "Jun 4", "violet"],
          ["Social Templates", "Bright Momentum", "15 sec", "3 stems", "May 29", "blue"],
        ].map(([name, track, duration, stems, modified, color]) => (
          <Card className="project-card" key={name}>
            <button className="project-visual" onClick={openStudio}><Waveform compact color={color as "violet" | "blue" | "teal" | "amber"} /><span>▶</span></button>
            <div className="project-body"><div><strong>{name}</strong><button>•••</button></div><span>{track}</span><div className="project-meta"><span>{duration}</span><i /><span>{stems}</span><i /><span>{modified}</span></div></div>
          </Card>
        ))}
      </div>
    </div>
  );
}

function TestLab() {
  const [running, setRunning] = useState(false);
  const categories = [
    ["CORE", ["Prompt Variety", "Duration Range"]],
    ["ADVANCED", ["Section Structure", "Shared BPM / Key / Grid", "Voiceover Ducking", "Stem Regeneration", "Seamless Looping"]],
    ["EDGE CASES", ["Very short duration", "Maximum duration", "Contradictory prompt", "Artist imitation request", "Vocal request", "Invalid duration", "Long narration silence", "Non-English prompt", "Concurrent jobs", "Provider failure"]],
  ];
  return (
    <div className="page">
      <div className="page-heading"><div><div className="eyebrow">QUALITY ENGINEERING</div><div className="page-title">Test Lab</div><div className="page-subtitle">Run repeatable end-to-end tests against the production music pipeline.</div></div><Button variant="primary" onClick={() => setRunning(!running)}>{running ? "Running Suite…" : "▷ Run Test Suite"}</Button></div>
      <Card className="test-summary">
        <div className="score-ring"><strong>94%</strong><span>PASS RATE</span></div>
        <div className="score-stat success"><strong>17</strong><span>PASSED</span></div><div className="score-stat danger"><strong>2</strong><span>FAILED</span></div><div className="score-stat"><strong>1</strong><span>SKIPPED</span></div>
        <div className="suite-meta"><span>LAST FULL RUN</span><strong>Today, 08:30</strong><small>14m 32s · Production</small></div>
      </Card>
      {running && <Card className="running-suite"><div><span className="spinner" /><div><strong>Running full regression suite</strong><small>Test 8 of 20 · Voiceover Ducking</small></div><b>40%</b></div><div className="large-progress"><span style={{ width: "40%" }} /></div></Card>}
      <Card className="tests-card">
        {categories.map(([category, tests]) => (
          <div className="test-group" key={category as string}>
            <div className="test-group-title">{category}</div>
            {(tests as string[]).map((test, index) => {
              const failed = test === "Provider failure" || test === "Contradictory prompt";
              return <div className="test-row" key={test}><span className={`test-icon ${failed ? "is-failed" : ""}`}>{failed ? "!" : "✓"}</span><div><strong>{test}</strong><small>{failed ? "Expected recovery behavior did not complete" : "All assertions completed successfully"}</small></div><span>Today, 08:{30 + index}</span><span>{(1.2 + index * 0.7).toFixed(1)}s</span><Badge tone={failed ? "danger" : "success"}>{failed ? "FAILED" : "PASSED"}</Badge><Button variant="ghost">Run Test</Button></div>;
            })}
          </div>
        ))}
      </Card>
    </div>
  );
}

export function Validation({
  job,
  onLoadJob,
}: {
  job: MusicJob | null;
  onLoadJob: (jobId: string) => void | Promise<void>;
}) {
  const [report, setReport] = useState<Ps1ValidationReport | null>(null);
  const [selectedRequirement, setSelectedRequirement] = useState<ValidationRequirement | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const loadReport = async () => {
    if (!job?.job_id || job.status !== "succeeded") {
      setReport(null);
      setError("");
      return;
    }

    setLoading(true);
    setReport(null);
    setError("");

    try {
      const next = await getPs1Validation(job.job_id);
      setReport(next);
    } catch (nextError) {
      setReport(null);
      setError(nextError instanceof ApiError ? nextError.message : "Unable to load PS1 validation.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setSelectedRequirement(null);
    void loadReport();
  }, [job?.job_id, job?.status, job?.updated_at, job?.result]);

  const handleRunValidation = async () => {
    if (!job?.job_id || job.status !== "succeeded") return;
    setRunning(true);
    setReport(null);
    setSelectedRequirement(null);
    setError("");
    try {
      const next = await runPs1Validation(job.job_id);
      setReport(next);
    } catch (nextError) {
      setError(nextError instanceof ApiError ? nextError.message : "Unable to run PS1 validation.");
    } finally {
      setRunning(false);
    }
  };

  const handleExportReport = () => {
    if (!job?.job_id) return;
    window.open(ps1ValidationReportUrl(job.job_id), "_blank", "noopener,noreferrer");
  };

  const badgeTone = report?.overall_status === "PASS"
    ? "success"
    : report?.overall_status === "WARNING"
      ? "warning"
      : report
        ? "danger"
        : "neutral";

  return (
    <div className="page">
      <div className="page-heading"><div><div className="eyebrow">REQUIREMENTS ASSESSMENT</div><div className="page-title">PS1 Validation</div><div className="page-subtitle">Evidence-backed compliance against product requirements.</div></div><div className="header-buttons"><Button onClick={handleExportReport} disabled={!job?.job_id || job.status !== "succeeded"}>Export Report</Button><Button variant="primary" onClick={() => void handleRunValidation()} disabled={!job?.job_id || job.status !== "succeeded" || running}>{running ? "Running…" : "Run Validation"}</Button></div></div>

      {!job && (
        <Card className="validation-overview"><div><span>ASSESSMENT STATUS</span><strong>No active job</strong><small>Generate or load a real music job to inspect backend validation evidence.</small></div><div className="validation-score">0<span>%</span></div><div className="overview-bar"><span style={{ width: "0%" }} /></div><Badge tone="neutral">EMPTY</Badge></Card>
      )}

      {job && job.status !== "succeeded" && (
        <Card className="validation-overview"><div><span>ASSESSMENT STATUS</span><strong>Validation unavailable</strong><small>Job {job.job_id} must succeed before PS1 validation can be loaded.</small></div><div className="validation-score">0<span>%</span></div><div className="overview-bar"><span style={{ width: "0%" }} /></div><Badge tone="neutral">PENDING</Badge></Card>
      )}

      {job?.status === "succeeded" && report && (
        <Card className="validation-overview"><div><span>ASSESSMENT STATUS</span><strong>{report.passed_count} of {report.requirements.length} passed</strong><small>Job {report.job_id} · Validated {formatDateTime(report.validated_at)}</small></div><div className="validation-score">{Math.round(report.percentage)}<span>%</span></div><div className="overview-bar"><span style={{ width: `${report.percentage}%` }} /></div><Badge tone={badgeTone}>{report.overall_status}</Badge><Badge tone="warning">{report.warning_count} WARNING</Badge><Badge tone="danger">{report.failed_count} FAILURE</Badge></Card>
      )}

      {loading && <Card className="requirements-card"><div style={{ padding: 20, color: "var(--muted)" }}>Loading backend validation…</div></Card>}
      {error && !loading && <Card className="requirements-card"><div style={{ padding: 20, color: "#ff8b8b" }}>{error}</div></Card>}

      {!loading && !error && report && (
        <Card className="requirements-card">
          <div className="requirements-head"><span>REQUIREMENT</span><span>EVIDENCE</span><span>TEST JOB</span><span>STATUS</span><span /></div>
          {report.requirements.map((requirement) => (
            <div className="requirement-row" key={requirement.requirement_id}>
              <button className="req-id req-button" type="button" onClick={() => setSelectedRequirement(requirement)}>{requirement.requirement_id}</button>
              <div><strong>{requirement.name}</strong><small>{requirement.category}</small></div>
              <span>{requirement.summary}</span>
              {requirement.associated_job_id ? (
                <button className="job-link" type="button" onClick={() => void onLoadJob(requirement.associated_job_id ?? "")}>{requirement.associated_job_id}</button>
              ) : (
                <span>—</span>
              )}
              <Badge tone={requirement.status === "PASS" ? "success" : requirement.status === "WARNING" ? "warning" : "danger"}>{requirement.status}</Badge>
              <Button variant="ghost" onClick={() => setSelectedRequirement(requirement)}>View Evidence</Button>
            </div>
          ))}
        </Card>
      )}

      <div className="credibility-note"><span>i</span><div><strong>Evidence policy</strong><small>The frontend renders the backend PS1 report directly. Requirements without persisted backend evidence remain warnings instead of assumed passes.</small></div></div>

      {selectedRequirement && (
        <div className="modal-backdrop" onMouseDown={() => setSelectedRequirement(null)}>
          <div className="modal manifest-modal" onMouseDown={(event) => event.stopPropagation()}>
            <div className="modal-header"><div><span>{selectedRequirement.requirement_id}</span><strong>{selectedRequirement.name}</strong></div><IconButton label="Close" onClick={() => setSelectedRequirement(null)}>×</IconButton></div>
            <div style={{ display: "grid", gap: 12 }}>
              <div className="detail-row"><span>Category</span><strong>{selectedRequirement.category}</strong></div>
              <div className="detail-row"><span>Status</span><strong>{selectedRequirement.status}</strong></div>
              <div className="detail-row"><span>Associated job</span><strong>{selectedRequirement.associated_job_id ?? "—"}</strong></div>
              <div style={{ color: "var(--muted)", fontSize: 12 }}>{selectedRequirement.summary}</div>
              <pre>{JSON.stringify(selectedRequirement.evidence, null, 2)}</pre>
            </div>
            <div className="modal-actions"><Button onClick={() => setSelectedRequirement(null)}>Close</Button>{selectedRequirement.associated_job_id && <Button variant="primary" onClick={() => void onLoadJob(selectedRequirement.associated_job_id ?? "")}>Open Job</Button>}</div>
          </div>
        </div>
      )}
    </div>
  );
}

function Settings() {
  return (
    <div className="page settings-page">
      <div className="page-heading"><div><div className="page-title">Settings</div><div className="page-subtitle">Configure generation infrastructure and organization-wide audio defaults.</div></div><Button variant="primary">Save Changes</Button></div>
      <div className="settings-layout">
        <div className="settings-menu">{["General", "Audio Defaults", "Generation", "Storage", "Team & Access"].map((item, index) => <button className={index === 0 ? "is-active" : ""} key={item}>{item}</button>)}</div>
        <div className="settings-content">
          <Card><SectionHeader title="Environment" detail="Connection settings for the music generation service." /><div className="settings-grid"><label><span>Environment</span><select defaultValue="Production"><option>Production</option><option>Staging</option></select><small>Changes affect all new generation jobs.</small></label><label><span>API endpoint</span><input defaultValue="https://api.incubrix.ai/v2" /><small>Connected · 38ms average latency</small></label></div><div className="security-note"><span>✓</span><div><strong>Credentials managed securely</strong><small>API secrets are stored in the server environment and are never displayed in the client.</small></div></div></Card>
          <Card><SectionHeader title="Audio Defaults" detail="Used as defaults for new projects and generation requests." /><div className="settings-grid"><label><span>Sample rate</span><select defaultValue="48"><option value="48">48,000 Hz</option><option value="44">44,100 Hz</option></select></label><label><span>Output format</span><select><option>WAV · 24-bit PCM</option><option>MP3 · 320 kbps</option></select></label><label><span>Loudness target</span><div className="unit-input"><input defaultValue="-16.0" /><span>LUFS</span></div></label><label><span>True peak limit</span><div className="unit-input"><input defaultValue="-1.0" /><span>dBTP</span></div></label></div></Card>
          <Card><SectionHeader title="Generation Provider" detail="Model routing and failure behavior." /><div className="provider-card"><div className="provider-logo">IC</div><div><strong>Cubrix Music v2.4</strong><small>Primary provider · healthy</small></div><Badge tone="success">CONNECTED</Badge><Button>Configure</Button></div><div className="setting-toggle"><div><strong>Automatic provider fallback</strong><small>Retry failed jobs with the configured secondary provider.</small></div><Toggle checked /></div></Card>
          <Card><SectionHeader title="Storage" detail="Generated file retention and package delivery." /><div className="usage"><div><strong>128.4 GB <span>of 500 GB</span></strong><small>Generated audio and project assets</small></div><span>26%</span></div><div className="large-progress"><span style={{ width: "26%" }} /></div></Card>
        </div>
      </div>
    </div>
  );
}

function DesignSystem() {
  return (
    <div className="page system-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">PRODUCT FOUNDATION</div>
          <div className="page-title">Cubrix Interface System</div>
          <div className="page-subtitle">Reusable tokens and interaction primitives for the Music Engine.</div>
        </div>
        <Badge tone="accent">VERSION 1.0</Badge>
      </div>
      <div className="system-grid">
        <Card className="system-card">
          <SectionHeader title="Color" detail="Semantic dark-mode palette" />
          <div className="swatches">
            {[
              ["Background", "swatch-bg"],
              ["Surface", "swatch-surface"],
              ["Accent", "swatch-accent"],
              ["Success", "swatch-success"],
              ["Warning", "swatch-warning"],
              ["Error", "swatch-error"],
            ].map(([name, className]) => <div key={name}><i className={className} /><span>{name}</span></div>)}
          </div>
        </Card>
        <Card className="system-card">
          <SectionHeader title="Typography" detail="Inter · functional hierarchy" />
          <div className="type-specimens"><div className="type-display">Display / 26</div><div className="type-heading">Section heading / 14</div><div className="type-body">Body copy is optimized for dense professional workflows.</div><div className="type-label">TECHNICAL LABEL / 08</div></div>
        </Card>
        <Card className="system-card system-card--wide">
          <SectionHeader title="Buttons & Status" detail="Action hierarchy and system feedback" />
          <div className="component-row">
            <Button variant="primary">Primary Action</Button><Button>Secondary</Button><Button variant="ghost">Ghost Action</Button><Button variant="danger">Destructive</Button><Button disabled>Disabled</Button>
            <Badge tone="success">SUCCEEDED</Badge><Badge tone="accent">PROCESSING</Badge><Badge tone="warning">WARNING</Badge><Badge tone="danger">FAILED</Badge>
          </div>
        </Card>
        <Card className="system-card">
          <SectionHeader title="Inputs" detail="Default, selected, and configured states" />
          <div className="system-inputs"><label><span>Project name</span><input defaultValue="Corporate Technology Theme" /></label><label><span>Output format</span><select><option>WAV · 24-bit</option></select></label><div><span>Automatic validation</span><Toggle checked /></div></div>
        </Card>
        <Card className="system-card">
          <SectionHeader title="Spacing & Shape" detail="4-point spatial rhythm" />
          <div className="spacing-spec"><div><i style={{ width: "16%" }} /><span>04</span></div><div><i style={{ width: "32%" }} /><span>08</span></div><div><i style={{ width: "64%" }} /><span>16</span></div><div><i style={{ width: "100%" }} /><span>24</span></div></div>
        </Card>
        <Card className="system-card system-card--wide">
          <SectionHeader title="Audio Components" detail="Waveforms, transport, tracks, and quality states" />
          <div className="audio-spec">
            <div className="audio-spec-wave"><Waveform compact /><span className="stem-playhead" /></div>
            <IconButton label="Previous">|◀</IconButton><button className="play-button">▶</button><IconButton label="Next">▶|</IconButton>
            <div className="meter"><i /><i /><i /><i /><i /><i /><i /></div>
            <Badge tone="success">BACKEND VERIFIED</Badge>
          </div>
        </Card>
      </div>
    </div>
  );
}

export default function App() {
  const [page, setPage] = useState<Page>("create");
  const [activeJob, setActiveJob] = useState<MusicJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [createError, setCreateError] = useState("");

  const startGeneration = async (request: CreateMusicSubmission) => {
    setSubmitting(true);
    setCreateError("");
    try {
      const specificationResponse = await createMusicSpecification({
        prompt: request.prompt,
        genre: request.genre,
        mood: request.mood,
        tempo: request.tempo,
        key: request.key,
        instruments: request.instruments,
        sections: request.sections,
      });
      const specification = specificationResponse.specification;
      const duration = specification.sections[specification.sections.length - 1]?.end || request.durationSeconds;

      const accepted = await generateMusicFromSpecification(specification);
      setActiveJob({
        job_id: accepted.job_id, status: "queued", progress: 0,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
        prompt: request.prompt, duration_s: duration, error: null, result: null,
      });
      setPage("progress");
      const completed = await waitForMusicJob(accepted.job_id, (nextJob) => setActiveJob(nextJob), 1000);
      if (completed.status === "failed") {
        setCreateError(completed.error?.message || "Music generation failed.");
        return;
      }

      if (request.ducking && request.voiceoverPath) {
        const duckAccepted = await createVoiceoverDuckingJob(accepted.job_id, request.voiceoverPath, 12.0, 50.0, 300.0);
        const duckCompleted = await waitForMusicJob(duckAccepted.job_id, (nextJob) => {
          // Child job status is progress, not parent QC evidence.
          setActiveJob(current => current ? { ...current, status: nextJob.status === "succeeded" ? "processing" : nextJob.status, progress: nextJob.progress, updated_at: nextJob.updated_at } : current);
        }, 1000);
        setActiveJob(await getMusicJob(accepted.job_id));
        if (duckCompleted.status === "failed") {
          setCreateError(duckCompleted.error?.message || "Voiceover ducking failed.");
        }
      }

      if (request.looping) {
        const loopAccepted = await createLoopJob(accepted.job_id, specification.tempo, 4);
        const loopCompleted = await waitForMusicJob(loopAccepted.job_id, (nextJob) => {
          setActiveJob(current => current ? { ...current, status: nextJob.status === "succeeded" ? "processing" : nextJob.status, progress: nextJob.progress, updated_at: nextJob.updated_at } : current);
        }, 1000);
        setActiveJob(await getMusicJob(accepted.job_id));
        if (loopCompleted.status === "failed") {
          setCreateError(loopCompleted.error?.message || "Seamless loop generation failed.");
        }
      }
    } catch (error) {
      setCreateError(error instanceof ApiError ? error.message : "Unable to start the music generation pipeline.");
    } finally {
      setSubmitting(false);
    }
  };

  const startSpecificationGeneration = async (specification: MusicSpecification) => {
    setCreateError("");
    try {
      const accepted = await generateMusicFromSpecification(specification);
      const duration = specification.sections[specification.sections.length - 1]?.end ?? 0;
      setActiveJob({
        job_id: accepted.job_id, status: "queued", progress: 0,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
        prompt: `Module 3 · ${specification.genre} · ${specification.mood.join(", ")}`,
        duration_s: duration, error: null, result: null,
      });
      setPage("progress");
      await waitForMusicJob(accepted.job_id, (nextJob) => setActiveJob(nextJob), 1000);
    } catch (error) {
      setCreateError(error instanceof ApiError ? error.message : "Unable to start Module 3 generation.");
    }
  };

  useEffect(() => {
    if (activeJob?.status === "succeeded") {
      // Keep the progress page visible so the user can see the completed job before opening Studio.
    }
  }, [activeJob?.status]);

  const openJobInWorkspace = async (jobId: string) => {
    const selected = await getMusicJob(jobId);
    if (selected.parent_job_id) {
      const parent = await getMusicJob(selected.parent_job_id);
      setActiveJob(parent);
      setPage(parent.status === "succeeded" ? "studio" : "progress");
      return;
    }

    setActiveJob(selected);
    setPage(selected.status === "succeeded" ? "studio" : "progress");
  };

  const content = {
    create: <CreateMusic onGenerate={(request) => void startGeneration(request)} submitting={submitting} error={createError} />,
    specification: <MusicSpecificationPage onGenerate={(specification) => void startSpecificationGeneration(specification)} />,
    progress: <GenerationProgress job={activeJob} onComplete={() => setPage(activeJob?.status === "succeeded" ? "studio" : "create")} onCancel={() => setPage("jobs")} />,
    studio: <Studio job={activeJob} onJobRefresh={setActiveJob} />,
    jobs: <Jobs openStudio={() => setPage("studio")} />,
    projects: <Projects openStudio={() => setPage("studio")} />,
    validation: <Validation job={activeJob} onLoadJob={(jobId) => void openJobInWorkspace(jobId)} />,
    testlab: <TestLab />,
    settings: <Settings />,
    system: <DesignSystem />,
  }[page];
  return <Shell page={page} setPage={setPage}>{content}</Shell>;
}
