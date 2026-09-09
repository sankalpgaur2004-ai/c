import { useState, useEffect, useRef } from "react";

/* ── Brand tokens ─────────────────────────────────────────────── */
const NAVY    = "#2D5A8C";
const CRIMSON = "#8B1A2B";
const BG      = "#F5F6FA";
const SURFACE = "#FFFFFF";
const BORDER  = "#E2E6F0";
const INK     = "#0F172A";
const MUTED   = "#4B5577";
const FAINT   = "#8B93AD";

/* ── Datasource definitions ───────────────────────────────────── */
interface SourceDef { id: string; label: string; logo: string; }

const SOURCES: SourceDef[] = [
  { id:"postgresql", label:"PostgreSQL", logo:"/postgre.svg"   },
  { id:"mysql",      label:"MySQL",      logo:"/mysql.png"      },
  { id:"snowflake",  label:"Snowflake",  logo:"/snowflake.png"  },
  { id:"databricks", label:"Databricks", logo:"/databricks.png" },
  { id:"powerbi",    label:"Power BI",   logo:"/powerbi.png"    },
  { id:"sharepoint", label:"SharePoint", logo:"/sharepoint.svg" },
  { id:"sqlite",     label:"SQLite",     logo:"/sqlite.svg"     },
  { id:"excel",      label:"Excel",      logo:"/excel.svg"      },
  { id:"documents",  label:"Documents",  logo:"/doc.png"        },
];

/* ── Static SVG mind map with animated spokes ────────────────── */
function MindMap() {
  const VW = 420, VH = 420;
  const cx = VW / 2, cy = VH / 2;
  const HUB_R  = 54;
  const ORBIT  = 158;

  const logoSize = (id: string) => id === "sqlite" ? 58 : 50;

  const nodes = SOURCES.map((src, i) => {
    const angle = (i / SOURCES.length) * Math.PI * 2 - Math.PI / 2;
    const LS  = logoSize(src.id);
    const gap = LS / 2 + 5;
    const nx  = cx + ORBIT * Math.cos(angle);
    const ny  = cy + ORBIT * Math.sin(angle);
    const x1  = cx + HUB_R * Math.cos(angle);
    const y1  = cy + HUB_R * Math.sin(angle);
    const x2  = cx + (ORBIT - gap) * Math.cos(angle);
    const y2  = cy + (ORBIT - gap) * Math.sin(angle);
    return { ...src, nx, ny, x1, y1, x2, y2, LS };
  });

  return (
    <svg viewBox={`0 0 ${VW} ${VH}`} style={{ width: "100%", height: "100%" }} xmlns="http://www.w3.org/2000/svg">
      <defs>
        <style>{`
          @keyframes dash { to { stroke-dashoffset: -20; } }
          .spoke { animation: dash 1.6s linear infinite; }
        `}</style>
      </defs>
      <circle cx={cx} cy={cy} r={ORBIT} fill="none" stroke={BORDER} strokeWidth="0.5" strokeOpacity="0.6" />
      {nodes.map((n, i) => (
        <line key={`line-${n.id}`} className="spoke"
          x1={n.x1} y1={n.y1} x2={n.x2} y2={n.y2}
          stroke={NAVY} strokeWidth="1" strokeOpacity="0.25"
          strokeLinecap="round" strokeDasharray="4 5"
          style={{ animationDelay: `${i * 0.18}s` }}
        />
      ))}
      <circle cx={cx} cy={cy} r={HUB_R + 10} fill="none" stroke={NAVY} strokeWidth="1" strokeOpacity="0.08" />
      <circle cx={cx} cy={cy} r={HUB_R + 5}  fill="none" stroke={NAVY} strokeWidth="1" strokeOpacity="0.12" />
      <circle cx={cx} cy={cy} r={HUB_R} fill={NAVY} />
      <circle cx={cx} cy={cy} r={HUB_R - 4} fill="none" stroke="rgba(255,255,255,0.12)" strokeWidth="1" />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="middle"
        fill="white" fontFamily="'DM Sans', sans-serif" fontSize="12" fontWeight="700" letterSpacing="-0.3"
      >ConvergeAI</text>
      {nodes.map(n => (
        <image key={`node-${n.id}`} href={n.logo}
          x={n.nx - n.LS / 2} y={n.ny - n.LS / 2}
          width={n.LS} height={n.LS}
          preserveAspectRatio="xMidYMid meet"
        />
      ))}
    </svg>
  );
}

/* ── Typed headline ───────────────────────────────────────────── */
function TypedHeadline() {
  const phrases = ["your databases.", "your documents.", "your dashboards.", "all your data."];
  const [phraseIdx, setPhraseIdx] = useState(0);
  const [displayed, setDisplayed] = useState("");
  const [deleting, setDeleting]   = useState(false);
  const timeout = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const target = phrases[phraseIdx];
    if (!deleting && displayed.length < target.length) {
      timeout.current = setTimeout(() => setDisplayed(target.slice(0, displayed.length + 1)), 65);
    } else if (!deleting && displayed.length === target.length) {
      timeout.current = setTimeout(() => setDeleting(true), 1800);
    } else if (deleting && displayed.length > 0) {
      timeout.current = setTimeout(() => setDisplayed(displayed.slice(0, -1)), 38);
    } else {
      setDeleting(false);
      setPhraseIdx((phraseIdx + 1) % phrases.length);
    }
    return () => { if (timeout.current) clearTimeout(timeout.current); };
  }, [displayed, deleting, phraseIdx]);

  return (
    <span style={{ color: NAVY }}>
      {displayed}
      <span style={{
        display: "inline-block", width: "2px", height: "0.85em",
        background: NAVY, marginLeft: "2px", verticalAlign: "text-bottom",
        animation: "blink 1s step-end infinite",
      }} />
    </span>
  );
}

/* ── Video modal ──────────────────────────────────────────────── */
function VideoModal({ onClose }: { onClose: () => void }) {
  const videoRef = useRef<HTMLVideoElement>(null);

  // Close on Escape key
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div
      onClick={onClose}
      style={{
        position: "fixed", inset: 0, zIndex: 100,
        background: "rgba(0,0,0,0.75)",
        display: "flex", alignItems: "center", justifyContent: "center",
        animation: "fadeIn 0.2s ease both",
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          position: "relative",
          width: "min(860px, 90vw)",
          borderRadius: "12px",
          overflow: "hidden",
          boxShadow: "0 24px 64px rgba(0,0,0,0.4)",
          background: "#000",
        }}
      >
        {/* Close button */}
        <button
          onClick={onClose}
          style={{
            position: "absolute", top: "10px", right: "12px", zIndex: 10,
            background: "rgba(0,0,0,0.55)", border: "none", borderRadius: "50%",
            width: "32px", height: "32px", cursor: "pointer",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <path d="M1 1l12 12M13 1L1 13" stroke="white" strokeWidth="2" strokeLinecap="round"/>
          </svg>
        </button>

        <video
          ref={videoRef}
          src="/ConvergeAI.mp4"
          controls
          autoPlay
          style={{ width: "100%", display: "block", maxHeight: "80vh" }}
        />
      </div>
    </div>
  );
}

/* ── Landing page ─────────────────────────────────────────────── */
export default function LandingPage() {
  const [showVideo, setShowVideo] = useState(false);

  return (
    <div style={{
      fontFamily: "'DM Sans', system-ui, sans-serif",
      background: BG,
      height: "100vh",
      overflow: "hidden",
      display: "flex",
      flexDirection: "column",
      color: INK,
    }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&display=swap');
        @keyframes blink  { 0%,100%{opacity:1} 50%{opacity:0} }
        @keyframes fadeIn { from{opacity:0;transform:translateY(10px)} to{opacity:1;transform:translateY(0)} }
        .anim-1 { animation: fadeIn 0.55s 0.05s both; }
        .anim-2 { animation: fadeIn 0.55s 0.18s both; }
        .anim-3 { animation: fadeIn 0.55s 0.30s both; }
        .cta-primary:hover  { transform:translateY(-2px); box-shadow:0 8px 22px rgba(45,90,140,0.28); }
        .cta-secondary:hover { border-color:${NAVY}!important; background:${BG}!important; }
      `}</style>

      {/* Video modal */}
      {showVideo && <VideoModal onClose={() => setShowVideo(false)} />}

      {/* Navbar */}
      <nav style={{
        flexShrink: 0,
        background: "rgba(255,255,255,0.93)",
        backdropFilter: "blur(12px)",
        borderBottom: `1px solid ${BORDER}`,
        display: "flex", alignItems: "center",
        padding: "0 2%", height: "56px",
      }}>
        <span style={{
          background: NAVY, color: "white",
          padding: "3px 14px", borderRadius: "20px",
          fontWeight: 700, fontSize: "14px", letterSpacing: "-0.01em",
        }}>ConvergeAI</span>
      </nav>

      {/* Main */}
      <main style={{
        flex: 1, minHeight: 0,
        display: "grid",
        gridTemplateColumns: "1fr 1fr",
        alignItems: "center",
        padding: "0 2%",
        gap: "16px",
      }}>
        {/* Left copy */}
        <div style={{ display: "flex", flexDirection: "column" }}>
          <img
            src="/CIRCULANTSLOGO.png"
            alt="Circulants"
            className="anim-1"
            style={{ height: "185px", width: "auto", objectFit: "contain", alignSelf: "flex-start", marginLeft: "-36px", marginBottom: "-10px" }}
          />

          <h1 className="anim-1" style={{
            fontSize: "clamp(32px,3.8vw,56px)", fontWeight: 700,
            lineHeight: 1.15, letterSpacing: "-0.02em", marginBottom: "16px",
          }}>
            One Platform to interact with<br />
            <TypedHeadline />
          </h1>

          <p className="anim-2" style={{
            fontSize: "17px", color: MUTED, lineHeight: 1.7,
            maxWidth: "480px", marginBottom: "24px",
          }}>
            Your data lives everywhere. <strong style={{ color: INK }}>ConvergeAI reaches all of it.</strong>
            <br />
            <strong style={{ color: INK }}>No code. No analyst dependency.</strong> Answers from every source.
            <br />
            <strong style={{ color: INK }}>DB · Documents · Dashboards</strong> — instant insights.
          </p>

          <div className="anim-3" style={{ display: "flex", gap: "10px", flexWrap: "wrap", marginBottom: "20px" }}>
            <a href="/home" className="cta-primary" style={{
              display: "inline-flex", alignItems: "center", gap: "10px",
              background: NAVY, color: "white",
              padding: "16px 36px", borderRadius: "11px",
              fontWeight: 600, fontSize: "19px",
              textDecoration: "none", transition: "all 0.22s",
            }}>
              Get started
              <svg width="18" height="18" viewBox="0 0 14 14" fill="none">
                <path d="M2 7h10M8 3l4 4-4 4" stroke="white" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </a>
          </div>
        </div>

        {/* Right — static SVG mind map */}
        <div style={{ height: "100%", minHeight: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <MindMap />
        </div>
      </main>

      {/* Footer */}
      <footer style={{
        flexShrink: 0, borderTop: `1px solid ${BORDER}`,
        padding: "12px 5%",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span style={{ fontSize: "11px", color: FAINT }}>© 2026 Circulants. All rights reserved.</span>
        <span style={{ fontSize: "11px", color: FAINT }}>Powered by Circulants</span>
      </footer>
    </div>
  );
}