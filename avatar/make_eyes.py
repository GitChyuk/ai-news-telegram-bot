#!/usr/bin/env python3
"""Аватарки под канал «Что там у ИИ 👀»: любопытные глаза в ИИ-стиле.

Круглая обрезка Telegram — вся мимика держится внутри вписанной окружности.
"""
import os
import cairosvg

OUT = os.path.dirname(os.path.abspath(__file__))
S = 512


def bg(c1, c2, glow):
    return f"""
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{c1}"/>
      <stop offset="100%" stop-color="{c2}"/>
    </linearGradient>
    <radialGradient id="gl" cx="50%" cy="42%" r="60%">
      <stop offset="0%" stop-color="{glow}" stop-opacity="0.5"/>
      <stop offset="100%" stop-color="{glow}" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="512" height="512" fill="url(#bg)"/>
  <circle cx="256" cy="215" r="250" fill="url(#gl)"/>"""


def eye(cx, cy, look_dx, look_dy, rx=86, ry=104, pr=44):
    """Белок + зрачок со смещением взгляда + блик."""
    px, py = cx + look_dx, cy + look_dy
    return f"""
  <ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="#F8FEFF"/>
  <circle cx="{px}" cy="{py}" r="{pr}" fill="#0B1020"/>
  <circle cx="{px+16}" cy="{py-16}" r="13" fill="#FFFFFF"/>
  <circle cx="{px-14}" cy="{py+18}" r="6" fill="#38BDF8"/>"""


def brow(cx, y, w, tilt):
    """Приподнятая бровь — добавляет выражение «а что это там?»."""
    x1, x2 = cx - w, cx + w
    return (f'<path d="M {x1} {y+tilt} Q {cx} {y-24} {x2} {y-tilt}" '
            f'fill="none" stroke="#F8FEFF" stroke-width="16" stroke-linecap="round"/>')


def curious():
    """Оба глаза скошены вверх-вбок — «подглядывает»."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 512 512">
{bg("#3B1E6E", "#0EA5B7", "#67E8F9")}
  {brow(170, 128, 62, 8)}
  {brow(342, 118, 62, -6)}
  {eye(170, 262, 26, -30)}
  {eye(342, 262, 26, -30)}
</svg>"""


def wide():
    """Большие круглые глаза в упор — удивлённо-любопытно, читается везде."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 512 512">
{bg("#0F172A", "#6D28D9", "#A78BFA")}
  {eye(168, 256, 8, -8, rx=92, ry=100, pr=50)}
  {eye(344, 256, 8, -8, rx=92, ry=100, pr=50)}
</svg>"""


for name, svg in (("eyes-curious", curious()), ("eyes-wide", wide())):
    open(os.path.join(OUT, f"{name}.svg"), "w").write(svg)
    for px in (512, 48):
        suffix = "" if px == 512 else "-48px"
        cairosvg.svg2png(bytestring=svg.encode(),
                         write_to=os.path.join(OUT, f"{name}{suffix}.png"),
                         output_width=px, output_height=px)
    print("готово:", name)
