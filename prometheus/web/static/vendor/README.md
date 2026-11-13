# Third-party files (served locally, never downloaded at run time)

| File | Project | Version | Licence |
|---|---|---|---|
| `highlight.min.js` | highlight.js (`@highlightjs/cdn-assets`) | 11.11.1 | BSD-3-Clause (`licenses/highlight.js-LICENSE.txt`) |
| `katex/` (js, css, woff2 fonts) | KaTeX | 0.16.22 | MIT (`licenses/katex-LICENSE.txt`) |
| `mermaid.min.js` | Mermaid | 11.12.1 | MIT (`licenses/mermaid-LICENSE.txt`) |

Loaded only when a message has code, maths or a diagram. Mermaid runs with `securityLevel: "strict"`, KaTeX with
`trust: false`; both read escaped text produced by `prometheus/web/render.py`.
