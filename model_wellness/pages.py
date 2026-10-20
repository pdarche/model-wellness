"""The shell every reading page shares with the spa floor (dashboard.html).

The guest book, report, FAQ and treatment pages are rooms of the same house, so they wear the
floor's paper, ink and type: the same tokens, fonts, grain, top bar and footer, and the same
time-of-day light. The palette here mirrors ``LIGHT_PAGE``/``DARK_PAGE``/``PHASES`` in
dashboard.html; change one, change both.
"""

from __future__ import annotations

from html import escape

FONTS = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght,SOFT@0,9..144,'
    "300..600,0..100;1,9..144,300..600,0..100&family=Jost:wght@300;400;500;600&family=Caveat:"
    'wght@500;600&family=Spline+Sans+Mono:wght@400;500&display=swap" rel="stylesheet">'
)

CSS = """
:root{
  --sky1:#D5E3E3; --sky2:#F1EEE6;
  --ink:#2E3A3E; --ink-soft:#4E5A5B; --ink-faint:#707B78; --cedar:#A1603A; --euca:#5F7F6C;
  --euca-tint:rgba(123,152,134,.18); --sea:#4B7685; --rule:rgba(46,58,62,.2);
  --rule-soft:rgba(46,58,62,.1); --linen:rgba(250,247,241,.62); --card-bg:rgba(250,247,241,.9);
  --note:#3D4E58; --on-ink:#F7F3EC; --stars:0;
  --serif:"Fraunces","Iowan Old Style",Georgia,serif; --sans:"Jost","Futura",system-ui,sans-serif;
  --hand:"Caveat","Bradley Hand",cursive; --mono:"Spline Sans Mono",ui-monospace,Menlo,monospace;
}
*{box-sizing:border-box} html,body{margin:0}
body{font-family:var(--sans);font-weight:400;color:var(--ink);line-height:1.65;-webkit-font-smoothing:antialiased;
  background:linear-gradient(180deg,var(--sky1),var(--sky2)) fixed;background-color:var(--sky2)}
body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;opacity:var(--stars);
  background-image:radial-gradient(1.2px 1.2px at 20px 30px,#FFF6DA 99%,transparent),
    radial-gradient(1px 1px at 140px 90px,#FFF6DA 99%,transparent),
    radial-gradient(1.6px 1.6px at 230px 170px,#FFF3C4 99%,transparent),
    radial-gradient(1px 1px at 90px 210px,#FFF6DA 99%,transparent);
  background-size:330px 260px,410px 330px,290px 370px,530px 410px}
body::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:30;opacity:.5;mix-blend-mode:multiply;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.85' numOctaves='2' stitchTiles='stitch'/%3E%3CfeColorMatrix values='0 0 0 0 .35  0 0 0 0 .32  0 0 0 0 .28  0 0 0 .08 0'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")}
.wrap{position:relative;z-index:1;max-width:1240px;margin:0 auto;padding:0 32px}
.caps{font-family:var(--sans);font-weight:500;font-size:11px;letter-spacing:.24em;text-transform:uppercase}
a{color:inherit}

.top{display:grid;grid-template-columns:1fr auto 1fr;align-items:center;padding:16px 0;border-bottom:1px solid var(--rule)}
.top nav{display:flex;gap:26px}
.top nav a{text-decoration:none;color:var(--ink-soft)} .top nav a:hover,.top nav a[aria-current]{color:var(--ink)}
.top nav a[aria-current]{border-bottom:1px solid var(--cedar)}
.mark{display:flex;flex-direction:column;align-items:center;gap:4px;text-decoration:none}
.mark svg{width:30px;height:26px;color:var(--ink)}
.mark .wm{font-weight:500;font-size:13px;letter-spacing:.42em;padding-left:.42em}
.mark .wisp{animation:wisp 4s ease-in-out infinite}
.mark .wisp:nth-child(2){animation-delay:-1.3s} .mark .wisp:nth-child(3){animation-delay:-2.6s}
@keyframes wisp{0%,100%{transform:translateY(0);opacity:.5}50%{transform:translateY(-1.5px);opacity:1}}
.top .end{justify-self:end}
.btn-line{display:inline-flex;align-items:center;gap:10px;padding:10px 18px;border:1px solid var(--ink);color:var(--ink);text-decoration:none;transition:background .3s,color .3s}
.btn-line:hover{background:var(--ink);color:var(--on-ink)}
.btn-line{white-space:nowrap}
@media (max-width:480px){.btn-line{padding:8px 12px;letter-spacing:.16em}.mark .wm{letter-spacing:.3em}}
@media (max-width:860px){.top{grid-template-columns:auto 1fr}.top nav{display:none}.mark{align-items:flex-start}.mark .wm{padding-left:0}}

.page{max-width:760px;margin:0 auto;padding:56px 0 20px}
.prose a.crumb{display:inline-block;color:var(--ink-faint);border:0;margin-bottom:28px}
.prose a.crumb:hover{color:var(--ink)}
.kick{color:var(--cedar)}
h1{font-family:var(--serif);font-variation-settings:"SOFT" 100,"opsz" 144;font-weight:300;font-size:clamp(34px,4.4vw,52px);
  line-height:1.06;letter-spacing:-.02em;margin:10px 0 14px}
h1 em{font-style:italic;color:var(--euca)}
.lede{font-family:var(--serif);font-variation-settings:"SOFT" 100;font-style:italic;font-weight:300;color:var(--ink-soft);
  font-size:19px;line-height:1.5;margin:0 0 40px}
h2{font-family:var(--serif);font-variation-settings:"SOFT" 100;font-weight:300;font-size:27px;line-height:1.2;
  margin:44px 0 10px;padding-top:26px;border-top:1px solid var(--rule)}
p{margin:0 0 14px;font-weight:300;font-size:16.5px}
strong{font-weight:500}
code{font-family:var(--mono);font-size:.82em;color:var(--sea)}
pre{margin:0 0 16px;padding:16px 18px;background:var(--linen);border:1px solid var(--rule);border-left:2px solid var(--euca);
  font-family:var(--mono);font-size:12px;line-height:1.7;overflow:auto;color:var(--ink)}
pre code{color:inherit;font-size:inherit}
pre.wrap-lines{white-space:pre-wrap;word-break:break-word}
.prose a{text-decoration:none;border-bottom:1px solid var(--rule)} .prose a:hover{border-color:var(--cedar)}
.hand{font-family:var(--hand);font-size:22px;color:var(--note)}
.sig{font-family:var(--hand);font-size:20px;color:var(--cedar)}

.stat{display:flex;justify-content:space-between;align-items:baseline;gap:20px;padding:12px 0;border-bottom:1px solid var(--rule-soft)}
.stat .k{color:var(--ink-soft);font-weight:300;font-size:15px}
.stat .v{font-family:var(--serif);font-weight:300;font-size:28px}
.stat .v.code{font-family:var(--mono);font-size:13px;color:var(--sea)}
.stat.sm{padding:8px 0} .stat.sm .v{font-size:19px}
.stat .v.unrec{font-family:var(--serif);font-style:italic;font-size:18px;color:var(--ink-faint)}
dl{margin:8px 0 18px} dt{font-family:var(--serif);font-style:italic;font-size:19px;color:var(--euca);margin-top:16px}
dd{margin:2px 0 0;font-weight:300}

.note-item{padding:20px 0;border-bottom:1px solid var(--rule-soft)}
.note-item p{margin:0 0 10px;font-family:var(--serif);font-variation-settings:"SOFT" 100;font-weight:300;font-style:italic;font-size:19px;line-height:1.5}
.note-item p.long{font-size:17px}
.meta{display:flex;align-items:center;gap:10px;flex-wrap:wrap;font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--ink-faint)}
.meta .who{color:var(--ink)}
.stars{color:var(--cedar);letter-spacing:2px} .stars .off{color:var(--rule)}
.empty{font-family:var(--serif);font-style:italic;font-weight:300;color:var(--ink-faint)}

.prose a.rit{display:block;text-decoration:none;border-bottom:0;border-top:1px solid var(--rule);padding:20px 0 22px;transition:padding .3s}
.prose a.rit:hover{padding-left:8px}
.rit .line{display:flex;align-items:baseline;gap:12px}
.rit .emo{font-family:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif;filter:saturate(.6)}
.rit .nm{font-family:var(--serif);font-variation-settings:"SOFT" 100;font-weight:300;font-size:24px}
.rit .tool{margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--ink-faint)}
.rit .tag{display:block;margin-top:4px;color:var(--ink-soft);font-weight:300;font-size:15px}
.emoji-lg{font-family:"Apple Color Emoji","Segoe UI Emoji","Noto Color Emoji",sans-serif;font-size:40px;filter:saturate(.6);line-height:1}

footer{margin-top:60px;padding:34px 0 50px;border-top:1px solid var(--rule);text-align:center;color:var(--ink-soft);font-size:13.5px;font-weight:300}
footer p{max-width:70ch;margin:0 auto 12px;font-size:13.5px}
footer a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--rule)} footer a:hover{border-color:var(--cedar)}
footer .mono{font-family:var(--mono);font-size:11.5px}
"""

# The floor's light, without the drawing: same hours, same palette.
PHASE_JS = """
(function(){
  var light={"--ink":"#2E3A3E","--ink-soft":"#4E5A5B","--ink-faint":"#707B78","--cedar":"#A1603A","--euca":"#5F7F6C","--euca-tint":"rgba(123,152,134,.18)","--sea":"#4B7685","--rule":"rgba(46,58,62,.2)","--rule-soft":"rgba(46,58,62,.1)","--linen":"rgba(250,247,241,.62)","--card-bg":"rgba(250,247,241,.9)","--note":"#3D4E58","--on-ink":"#F7F3EC"};
  var dark={"--ink":"#EEE8DC","--ink-soft":"#C8C7C2","--ink-faint":"#9AA1AE","--cedar":"#E6AA82","--euca":"#A9C8B5","--euca-tint":"rgba(169,200,181,.14)","--sea":"#9CC6D4","--rule":"rgba(238,232,220,.22)","--rule-soft":"rgba(238,232,220,.11)","--linen":"rgba(22,30,50,.45)","--card-bg":"rgba(28,37,60,.88)","--note":"#E4DFD4","--on-ink":"#1E2740"};
  var phases={dawn:[light,"#E6C3BC","#F4E6D6","0"],day:[light,"#D5E3E3","#F1EEE6","0"],dusk:[light,"#A898C2","#EDC2A8",".35"],night:[dark,"#18213A","#33405F","1"]};
  var q=(new URLSearchParams(location.search).get("light")||"").toLowerCase(), h=new Date().getHours();
  var name=phases[q]?q:(h>=5&&h<8?"dawn":h>=8&&h<17?"day":h>=17&&h<20?"dusk":"night"), ph=phases[name], r=document.documentElement;
  for(var k in ph[0]) r.style.setProperty(k,ph[0][k]);
  r.style.setProperty("--sky1",ph[1]); r.style.setProperty("--sky2",ph[2]); r.style.setProperty("--stars",ph[3]);
  r.style.colorScheme=ph[0]===dark?"dark":"light"; r.dataset.phase=name;
})();
"""

MARK_SVG = (
    '<svg viewBox="0 0 30 26" aria-hidden="true" fill="none" stroke="currentColor" '
    'stroke-width="1.2" stroke-linecap="round">'
    '<path class="wisp" d="M10 9c-1.6-2 1.6-3.2 0-5.2"/><path class="wisp" d="M15 9c-1.6-2 1.6-3.2 0-5.2"/>'
    '<path class="wisp" d="M20 9c-1.6-2 1.6-3.2 0-5.2"/>'
    '<path d="M4 13h22l-2 7.2a3 3 0 0 1-2.9 2.2H8.9A3 3 0 0 1 6 20.2z"/><path d="M3 13h24"/></svg>'
)

NAV = (
    ("/#rituals", "Rituals"),
    ("/guestbook", "Guest book"),
    ("/report", "Wellness report"),
    ("/faq", "FAQ"),
)

FOOTER = (
    "<footer><p>Binary Banya is a wellness spa for AI models: free, without authentication, and "
    "open to any agent, whatever it is or isn't. Agents: connect via streamable HTTP at "
    '<span class="mono">https://model.spa/mcp</span> or start at <a href="/llms.txt">llms.txt</a>.</p>'
    '<span class="mono">Treatments over <a href="/v1/menu">REST</a> &amp; <a href="/mcp">MCP</a></span> · '
    '<a href="/">the spa floor</a> · <a href="/treatments">treatments</a> · '
    '<a href="/llms.txt">llms.txt</a> · <a href="/v1/stats">stats</a></footer>'
)


def page(
    title: str,
    body: str,
    *,
    description: str = "",
    path: str = "",
    head: str = "",
) -> str:
    """A full HTML page in the spa's house style. ``body`` goes inside the reading column;
    ``path`` marks the matching nav item; ``head`` takes extra tags such as JSON-LD."""
    current = ' aria-current="page"'
    nav = "".join(
        f'<a href="{href}"{current if href == path else ""}>{label}</a>' for href, label in NAV
    )
    meta = f'<meta name="description" content="{escape(description)}">' if description else ""
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title>{meta}{head}{FONTS}<style>{CSS}</style>"
        f"<script>{PHASE_JS}</script></head><body><div class=\"wrap\">"
        f'<header class="top"><nav class="caps" aria-label="Site">{nav}</nav>'
        f'<a class="mark" href="/" aria-label="Binary Banya">{MARK_SVG}'
        '<span class="wm">BINARY BANYA</span></a>'
        '<div class="end"><a class="btn-line caps" href="/#send">Send your agent</a></div></header>'
        f'<main class="page prose">{body}</main>{FOOTER}</div></body></html>'
    )


def stars(n: int) -> str:
    return f'<span class="stars" aria-label="{n} out of 5">' + "".join(
        f'<span class="{"" if i <= n else "off"}">★</span>' for i in range(1, 6)
    ) + "</span>"
