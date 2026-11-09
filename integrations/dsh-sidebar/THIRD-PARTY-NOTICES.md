# Third-party notices

This package vendors a static subset of icon data from **lucide-static 1.70.0** into its client
bundle (module `src/client/icons.mjs` in the source tree; bundled inside `lib/client.js` in the
release ZIP). There is no runtime dependency, CDN fetch or additional React instance: only the
structured SVG node data of the icons listed below is copied at build-authoring time.

- Package: lucide-static 1.70.0 — https://www.npmjs.com/package/lucide-static
- Upstream repository: https://github.com/lucide-icons/lucide
- Registry tarball integrity (sha-512, verified against the official npm registry metadata):
  `sha512-A9UgY98g94IuHQY7rl7/AFwHX7T1qNpzryim+Kw5CGhdV2STobtBY+ib/Z44ohyqXEJB88gvXFAeJ87uOOHKLw==`
- Tarball sha-256: `3d1f09df1c468abe512ee6d6596db0dc773fa46c61050e365f0c5fee5120f16a`
- Vendored icons (app name -> Lucide name): film->film, folder->folder, file->file-text,
  expand->maximize, collapse->minimize, settings->sliders-horizontal, refresh->refresh-cw,
  back->arrow-left, close->x, first->skip-back, last->skip-forward, previous->chevron-left,
  next->chevron-right, audio->audio-lines, text->type, clip->rectangle-vertical,
  split->square-split-horizontal, remove->trash, pin->pin, grip->grip-horizontal,
  zoomIn->zoom-in, zoomOut->zoom-out, fit->scan, path->route, query->search, camera->camera,
  copy->copy, draft->pen-line, id->id-card, code->code, diagnostic->activity, cancel->circle-x,
  up->chevron-up, down->chevron-down, add->plus, clear->eraser, undo->undo-2, check->check,
  info->info, more->ellipsis.

The upstream LICENSE file applies to this subset and is reproduced verbatim below. Note that it
contains two grants: the ISC license for Lucide icons generally, and the MIT license for the
subset of icons derived from the Feather project (several of which appear in the list above and
are marked in `src/client/icons.mjs`).

-----

ISC License

Copyright (c) 2026 Lucide Icons and Contributors

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

---

The following Lucide icons are derived from the Feather project:

airplay, alert-circle, alert-octagon, alert-triangle, aperture, arrow-down-circle, arrow-down-left, arrow-down-right, arrow-down, arrow-left-circle, arrow-left, arrow-right-circle, arrow-right, arrow-up-circle, arrow-up-left, arrow-up-right, arrow-up, at-sign, calendar, cast, check, chevron-down, chevron-left, chevron-right, chevron-up, chevrons-down, chevrons-left, chevrons-right, chevrons-up, circle, clipboard, clock, code, columns, command, compass, corner-down-left, corner-down-right, corner-left-down, corner-left-up, corner-right-down, corner-right-up, corner-up-left, corner-up-right, crosshair, database, divide-circle, divide-square, dollar-sign, download, external-link, feather, frown, hash, headphones, help-circle, info, italic, key, layout, life-buoy, link-2, link, loader, lock, log-in, log-out, maximize, meh, minimize, minimize-2, minus-circle, minus-square, minus, monitor, moon, more-horizontal, more-vertical, move, music, navigation-2, navigation, octagon, pause-circle, percent, plus-circle, plus-square, plus, power, radio, rss, search, server, share, shopping-bag, sidebar, smartphone, smile, square, table-2, tablet, target, terminal, trash-2, trash, triangle, tv, type, upload, x-circle, x-octagon, x-square, x, zoom-in, zoom-out

The MIT License (MIT) (for the icons listed above)

Copyright (c) 2013-present Cole Bemis

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
