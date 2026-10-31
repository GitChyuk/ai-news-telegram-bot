#!/usr/bin/env python3
"""Генерация аватарок канала: SVG -> PNG 512x512.

Telegram обрезает аватар в круг, поэтому вся смысловая графика держится
внутри вписанной окружности, а фон рисуется на весь квадрат.
"""
import os

import cairosvg

OUT = os.path.dirname(os.path.abspath(__file__))
S = 512
C = S / 2

BG = """
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%%" stop-color="%s"/>
      <stop offset="100%%" stop-color="%s"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%%" cy="50%%" r="50%%">
      <stop offset="0%%" stop-color="%s" stop-opacity="0.55"/>
      <stop offset="100%%" stop-color="%s" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="512" height="512" fill="url(#bg)"/>
  <circle cx="256" cy="256" r="250" fill="url(#glow)"/>
"""


def signal():
    """Нейрон, вещающий волнами: узел + расходящиеся дуги."""
    arcs = []
    for i, (r, w, o) in enumerate([(96, 22, 0.95), (146, 20, 0.6), (196, 18, 0.3)]):
        # Дуги слева и справа от центрального узла.
        for sign in (1, -1):
            x1, y1 = C + sign * r * 0.62, C - r * 0.78
            x2, y2 = C + sign * r * 0.62, C + r * 0.78
            sweep = 1 if sign > 0 else 0
            arcs.append(
                f'<path d="M {x1:.1f} {y1:.1f} A {r} {r} 0 0 {sweep} {x2:.1f} {y2:.1f}" '
                f'fill="none" stroke="#67E8F9" stroke-opacity="{o}" '
                f'stroke-width="{w}" stroke-linecap="round"/>'
            )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 512 512">
{BG % ("#1E1B4B", "#4338CA", "#22D3EE", "#22D3EE")}
  {''.join(arcs)}
  <circle cx="256" cy="256" r="46" fill="#F0FDFF"/>
  <circle cx="256" cy="256" r="46" fill="none" stroke="#22D3EE" stroke-width="6" stroke-opacity="0.7"/>
</svg>"""


def monogram():
    """Жирная монограмма AI — читается на любом размере."""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 512 512">
{BG % ("#0F172A", "#1D4ED8", "#38BDF8", "#38BDF8")}
  <circle cx="256" cy="256" r="186" fill="none" stroke="#38BDF8" stroke-width="10" stroke-opacity="0.45"/>
  <text x="256" y="258" font-family="DejaVu Sans" font-weight="bold" font-size="210"
        fill="#FFFFFF" text-anchor="middle" dominant-baseline="central"
        letter-spacing="-6">AI</text>
  <circle cx="256" cy="70" r="17" fill="#38BDF8"/>
  <circle cx="256" cy="442" r="17" fill="#38BDF8"/>
</svg>"""


def graph():
    """Граф нейросети: три слоя узлов со связями."""
    layers = [
        [(150, 176), (150, 256), (150, 336)],
        [(256, 200), (256, 312)],
        [(362, 176), (362, 256), (362, 336)],
    ]
    edges = []
    for a, b in ((0, 1), (1, 2)):
        for (x1, y1) in layers[a]:
            for (x2, y2) in layers[b]:
                edges.append(
                    f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                    f'stroke="#7DD3FC" stroke-opacity="0.45" stroke-width="5"/>'
                )
    nodes = []
    for i, layer in enumerate(layers):
        fill = "#FFFFFF" if i == 1 else "#67E8F9"
        r = 30 if i == 1 else 25
        for (x, y) in layer:
            nodes.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{fill}"/>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{S}" height="{S}" viewBox="0 0 512 512">
{BG % ("#111827", "#4C1D95", "#A78BFA", "#A78BFA")}
  {''.join(edges)}
  {''.join(nodes)}
</svg>"""


for name, svg in (("1-signal", signal()), ("2-monogram", monogram()), ("3-graph", graph())):
    with open(os.path.join(OUT, f"{name}.svg"), "w", encoding="utf-8") as f:
        f.write(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=os.path.join(OUT, f"{name}.png"),
                     output_width=512, output_height=512)
    # Превью размером с иконку в списке чатов — главная проверка на читаемость.
    cairosvg.svg2png(bytestring=svg.encode(), write_to=os.path.join(OUT, f"{name}-40px.png"),
                     output_width=40, output_height=40)
    print("готово:", name)
